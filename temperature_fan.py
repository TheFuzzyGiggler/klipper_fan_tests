# Support fans that are enabled when temperature exceeds a set threshold
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Heavily modified to support additional test features for potential import
# mainline Klipper
#
# Modifications Copyright (C) 2024 Nick Chelf <nickc84@googlemail.com
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from . import fan
import math

KELVIN_TO_CELSIUS = -273.15
MAX_FAN_TIME = 5.0
AMBIENT_TEMP = 25.
PID_PARAM_BASE = 255.
MAX_TEMP_BUFFER = .8

class TemperatureFan:
    def __init__(self, config):
        self.name = config.get_name().split()[1]
        self.printer = config.get_printer()
        self.fan = fan.Fan(config, default_shutdown_speed=1.)

        ########## Temp Settings ##########
        self.min_temp = config.getfloat('min_temp', minval=KELVIN_TO_CELSIUS)
        if self.min_temp > AMBIENT_TEMP * .9:
            tempwarning = f"!!!Warning: Minimum temp of {self.min_temp: .3f} "
            tempwarning += " is close to or above room temperature. \n"
            tempwarning += "ADC Shutdown likely!"
            self.printer.lookup_object('gcode').respond_raw(tempwarning)
        self.max_temp = config.getfloat('max_temp', above=self.min_temp)
        self.min_temp_cutoff = config.getfloat('min_temp_cutoff', default=0, maxval=65)
        self.target_temp_conf = config.getfloat(
            'target_temp', 40. if self.max_temp > 40. else self.max_temp,
            minval=self.min_temp, maxval=self.max_temp)
        self.target_temp = self.target_temp_conf
        self.last_temp = 0.
        self.last_temp_time = 0.
        ###################################

        ######### Heater Settings #########
        pheaters = self.printer.load_object(config, 'heaters')
        self.heaters = []
        ###################################


        ####### Temp Sensor Settings ######
        self.sensor = pheaters.setup_sensor(config)
        self.sensor.setup_minmax(self.min_temp, self.max_temp)
        self.sensor.setup_callback(self.temperature_callback)
        pheaters.register_sensor(config, self)
        ###################################

        ########## Speed Control ##########
        algos = {'watermark': ControlBangBang, 'pid': ControlPID, 'slope': ControlSlope}
        algo = config.getchoice('control', algos)
        self.control = algo(self, config)
        self.next_speed_time = 0.
        self.last_speed_value = 0.
        self.speed_delay = self.sensor.get_report_time_delta()
        self.max_speed_conf = config.getfloat(
            'max_speed', 1., above=0., maxval=1.)
        self.max_speed = self.max_speed_conf
        self.min_speed_conf = config.getfloat(
            'min_speed', 0.3, minval=0., maxval=1.)
        self.min_speed = self.min_speed_conf
        ###################################

        self.slicer_fan_num = config.getint('slicer_fan_number', default=None)
        if self.slicer_fan_num is not None:
            self.printer.lookup_object('fan').add_fan(self.slicer_fan_num,self.fan)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_mux_command(
            "SET_TEMPERATURE_FAN_TARGET", "TEMPERATURE_FAN", self.name,
            self.cmd_SET_TEMPERATURE_FAN_TARGET,
            desc=self.cmd_SET_TEMPERATURE_FAN_TARGET_help)

    def set_speed(self, read_time, value):
        if value <= 0.:
            value = 0.
        elif value < self.min_speed:
            value = self.min_speed
        if self.target_temp <= 0.:
            value = 0.
        if ((read_time < self.next_speed_time or not self.last_speed_value)
                and abs(value - self.last_speed_value) < 0.05):
            # No significant change in value - can suppress update
            return
        speed_time = read_time + self.speed_delay
        self.next_speed_time = speed_time + 0.75 * MAX_FAN_TIME
        self.last_speed_value = value
        self.fan.set_speed(speed_time, value)
    def temperature_callback(self, read_time, temp):
        self.last_temp = temp
        self.control.temperature_callback(read_time, temp)
    def get_temp(self, eventtime):
        return self.last_temp, self.target_temp
    def get_min_speed(self):
        return self.min_speed
    def get_max_speed(self):
        return self.max_speed

    def get_status(self, eventtime):
        status = self.fan.get_status(eventtime)
        status["temperature"] = round(self.last_temp, 2)
        status["target"] = self.target_temp
        return status
    cmd_SET_TEMPERATURE_FAN_TARGET_help = \
        "Sets a temperature fan target and fan speed limits"
    def cmd_SET_TEMPERATURE_FAN_TARGET(self, gcmd):
        temp = gcmd.get_float('TARGET', self.target_temp_conf)
        self.set_temp(temp)
        min_speed = gcmd.get_float('MIN_SPEED', self.min_speed)
        max_speed = gcmd.get_float('MAX_SPEED', self.max_speed)
        if min_speed > max_speed:
            raise self.printer.command_error(
                "Requested min speed (%.1f) is greater than max speed (%.1f)"
                % (min_speed, max_speed))
        self.set_min_speed(min_speed)
        self.set_max_speed(max_speed)

    def set_temp(self, degrees):
        if degrees and (degrees < self.min_temp or degrees > self.max_temp):
            raise self.printer.command_error(
                "Requested temperature (%.1f) out of range (%.1f:%.1f)"
                % (degrees, self.min_temp, self.max_temp))
        self.target_temp = degrees

    def set_min_speed(self, speed):
        if speed and (speed < 0. or speed > 1.):
            raise self.printer.command_error(
                "Requested min speed (%.1f) out of range (0.0 : 1.0)"
                % (speed))
        self.min_speed = speed

    def set_max_speed(self, speed):
        if speed and (speed < 0. or speed > 1.):
            raise self.printer.command_error(
                "Requested max speed (%.1f) out of range (0.0 : 1.0)"
                % (speed))
        self.max_speed = speed


######################################################################
# Bang-bang control algo
######################################################################

class ControlBangBang:
    def __init__(self, temperature_fan, config):
        self.temperature_fan = temperature_fan
        self.max_delta = config.getfloat('max_delta', 2.0, above=0.)
        self.heating = False
    def temperature_callback(self, read_time, temp):
        if temp < self.temperature_fan.min_temp_cutoff:
            self.temperature_fan.set_speed(read_time,0)
            return
        current_temp, target_temp = self.temperature_fan.get_temp(read_time)
        if (self.heating
            and temp >= target_temp+self.max_delta):
            self.heating = False
        elif (not self.heating
              and temp <= target_temp-self.max_delta):
            self.heating = True
        if self.heating:
            self.temperature_fan.set_speed(read_time, 0.)
        else:
            self.temperature_fan.set_speed(read_time,
                                           self.temperature_fan.get_max_speed())

######################################################################
# Proportional Integral Derivative (PID) control algo
######################################################################

PID_SETTLE_DELTA = 1.
PID_SETTLE_SLOPE = .1

class ControlPID:
    def __init__(self, temperature_fan, config):
        self.temperature_fan = temperature_fan
        self.Kp = config.getfloat('pid_Kp') / PID_PARAM_BASE
        self.Ki = config.getfloat('pid_Ki') / PID_PARAM_BASE
        self.Kd = config.getfloat('pid_Kd') / PID_PARAM_BASE
        self.min_deriv_time = config.getfloat('pid_deriv_time', 2., above=0.)
        self.temp_integ_max = 0.
        if self.Ki:
            self.temp_integ_max = self.temperature_fan.get_max_speed() / self.Ki
        self.prev_temp = AMBIENT_TEMP
        self.prev_temp_time = 0.
        self.prev_temp_deriv = 0.
        self.prev_temp_integ = 0.
    def temperature_callback(self, read_time, temp):
        if temp < self.temperature_fan.min_temp_cutoff:
            self.temperature_fan.set_speed(read_time,0)
            return
        current_temp, target_temp = self.temperature_fan.get_temp(read_time)
        time_diff = read_time - self.prev_temp_time
        # Calculate change of temperature
        temp_diff = temp - self.prev_temp
        if time_diff >= self.min_deriv_time:
            temp_deriv = temp_diff / time_diff
        else:
            temp_deriv = (self.prev_temp_deriv * (self.min_deriv_time-time_diff)
                          + temp_diff) / self.min_deriv_time
        # Calculate accumulated temperature "error"
        temp_err = target_temp - temp
        temp_integ = self.prev_temp_integ + temp_err * time_diff
        temp_integ = max(0., min(self.temp_integ_max, temp_integ))
        # Calculate output
        co = self.Kp*temp_err + self.Ki*temp_integ - self.Kd*temp_deriv
        bounded_co = max(0., min(self.temperature_fan.get_max_speed(), co))
        self.temperature_fan.set_speed(
            read_time, max(self.temperature_fan.get_min_speed(),
                           self.temperature_fan.get_max_speed() - bounded_co))
        # Store state for next measurement
        self.prev_temp = temp
        self.prev_temp_time = read_time
        self.prev_temp_deriv = temp_deriv
        if co == bounded_co:
            self.prev_temp_integ = temp_integ


######################################################################
# Slope control algo
######################################################################

class ControlSlope:
    def __init__(self, temperature_fan, config):
        self.temperature_fan = temperature_fan
        self.hysteresis_margin = 0.5
        self.min_speed = self.temperature_fan.min_speed
        self.min_temp_cutoff = self.temperature_fan.min_temp_cutoff
        self.min_temp = self.temperature_fan.min_temp
        slopetype = {'linear': self.linear, 'log': self.log, 'exponential': self.exponential}
        self.algo = config.getchoice('slope', slopetype)
        #Keeps curve maximum away from max_temp for safety
        self.max_temp = self.temperature_fan.max_temp * MAX_TEMP_BUFFER
        # Setting too low of a min_temp skews the slope
        # Default lowest is ambient room temp or the min_temp_cutoff
        if self.min_temp < AMBIENT_TEMP \
                or self.min_temp < self.min_temp_cutoff:
            if self.min_temp_cutoff > AMBIENT_TEMP:
                self.min_temp = self.min_temp_cutoff
            else:
                self.min_temp = AMBIENT_TEMP

    def temperature_callback(self, read_time, temp):
        if temp < self.temperature_fan.min_temp_cutoff - self.hysteresis_margin:
            # Temperature is significantly below the cutoff, turn off the fan
            self.temperature_fan.set_speed(read_time, 0)
            return
        elif temp > self.temperature_fan.min_temp_cutoff + self.hysteresis_margin:
            # Temperature is above the hysteresis high threshold, proceed with normal processing
            temp = max(self.min_temp, min(temp, self.max_temp))
            self.temperature_fan.target_temp = math.trunc(temp * 10) / 10
            self.algo(read_time, temp)

    def linear(self, read_time, temp):
        # Calculate the proportion of the temperature within the range
        proportion = (temp - self.min_temp) / (self.max_temp - self.min_temp)
        # Linearly interpolate the fan speed
        speed = (self.temperature_fan.max_speed - self.temperature_fan.min_speed) * proportion
        if speed > self.min_speed:
            self.temperature_fan.set_speed(read_time,speed)
        else:
            self.temperature_fan.set_speed(read_time,self.min_speed)

    def log(self, read_time, temp):
        # Offset the temperature range to start at 1 (to avoid log(0))
        offset_min_temp = 1
        offset_max_temp = self.max_temp - self.min_temp + offset_min_temp
        # Offset and normalize the input temperature
        offset_temp = temp - self.min_temp + offset_min_temp
        normalized_temp = math.log(offset_temp) / math.log(offset_max_temp)
        # Calculate the fan speed based on the normalized temperature
        speed = (self.temperature_fan.max_speed - self.temperature_fan.min_speed) * normalized_temp
        if speed > self.min_speed:
            self.temperature_fan.set_speed(read_time, speed)
        else:
            self.temperature_fan.set_speed(read_time, self.min_speed)
    def exponential(self, read_time, temp):
        # Normalize the temperature to a 0-1 scale
        normalized_temp = (temp - self.min_temp) / (self.max_temp -self.min_temp)
        # Apply the exponential formula directly to calculate speed
        speed = (self.temperature_fan.max_speed - self.temperature_fan.min_speed) * normalized_temp ** 2
        if speed > self.min_speed:
            self.temperature_fan.set_speed(read_time, speed)
        else:
            self.temperature_fan.set_speed(read_time, self.min_speed)

def load_config_prefix(config):
    return TemperatureFan(config)

