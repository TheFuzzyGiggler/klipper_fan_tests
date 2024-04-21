# Printer cooling fan
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from . import pulse_counter

FAN_MIN_TIME = 0.100

class Fan:
    def __init__(self, config, default_shutdown_speed=0.):
        self.printer = config.get_printer()
        self.config = config
        self.last_fan_value = 0.
        self.last_fan_time = 0.
        self.fan_name = config.get_name().split()

        # Read config
        self.slicer_fan_num = self.config.getint('slicer_fan_number', default=None)
        self.max_power = config.getfloat('max_power', 1., above=0., maxval=1.)
        self.kick_start_time = config.getfloat('kick_start_time', 0.1,
                                               minval=0.)
        self.off_below = config.getfloat('off_below', default=0.,
                                         minval=0., maxval=1.)
        cycle_time = config.getfloat('cycle_time', 0.010, above=0.)
        hardware_pwm = config.getboolean('hardware_pwm', False)
        shutdown_speed = config.getfloat(
            'shutdown_speed', default_shutdown_speed, minval=0., maxval=1.)
        # Setup pwm object
        ppins = self.printer.lookup_object('pins')
        self.mcu_fan = ppins.setup_pin('pwm', config.get('pin'))
        self.mcu_fan.setup_max_duration(0.)
        self.mcu_fan.setup_cycle_time(cycle_time, hardware_pwm)
        shutdown_power = max(0., min(self.max_power, shutdown_speed))
        self.mcu_fan.setup_start_value(0., shutdown_power)
        self.pwm_fan = False
        self.enable_pin = None
        enable_pin = config.get('enable_pin', None)
        if enable_pin is not None:
            self.enable_pin = ppins.setup_pin('digital_out', enable_pin)
            self.enable_pin.setup_max_duration(0.)
            #Enable 4 wire fan control, changes PWM curve below
            self.pwm_fan = True
        # Setup tachometer
        self.tachometer = FanTachometer(config, self)
        # Register callbacks
        self.printer.register_event_handler("gcode:request_restart",
                                            self._handle_request_restart)

        if len(self.fan_name)>1:
            gcode = self.printer.lookup_object("gcode")
            gcode.register_mux_command("SET_FAN_SPEED", "FAN",
                                    self.fan_name[1],
                                    self.cmd_SET_FAN_SPEED,
                                    desc=self.cmd_SET_FAN_SPEED_help)
        self.printer.register_event_handler("klippy:connect", self.handle_connect)

    def handle_connect(self):
        try:
            self.printer_fan = self.printer.lookup_object('fan')
        except Exception:
            self.printer_fan = None

        self.slicer_fan_num = (
            self.config.getint('slicer_fan_number', default=None))
        if self.slicer_fan_num is not None:
            (self.printer.lookup_object('fan')
             .add_fan(self.slicer_fan_num, self))
        elif (self.printer_fan is not None and self.fan_name[0] == 'fan'
              and len(self.fan_name)>1):
            warning = f"[fan] is already configured {' '.join(self.fan_name)}"
            warning += f" requires slicer_fan_number to be set"
            raise self.printer.config_error(warning)

    def get_mcu(self):
        return self.mcu_fan.get_mcu()

    cmd_SET_FAN_SPEED_help = "Sets the speed of a fan"

    def cmd_SET_FAN_SPEED(self, gcmd):
        speed = gcmd.get_float('SPEED', 0.)
        self.set_speed_from_command(speed)

    def set_speed(self, print_time, value):
        #Check to see if 4 wire fan
        if not self.pwm_fan:
            #If fan is not a 4 wire fan with built-in PWM circuitry then
            #scale the value so no PWM below 20% duty cycle.
            #This complies with Intel standard of PWM fans (the defacto standard)
            #See page 14 of "intel-4wire-pwn-fans-specs.pdf"
            #Effectively "normalizes" PWM duty cycle vs. fan RPM
            fan_speed = .2 + .8 * value
            fan_speed = 0 if fan_speed <= .2 else fan_speed
        else:
            fan_speed = value
        if value < self.off_below:
            fan_speed = 0.
        fan_speed = max(0., min(self.max_power, fan_speed * self.max_power))
        if value == self.last_fan_value:
            return
        print_time = max(self.last_fan_time + FAN_MIN_TIME, print_time)
        if self.enable_pin:
            if value > 0 and self.last_fan_value == 0:
                self.enable_pin.set_digital(print_time, 1)
            elif value == 0 and self.last_fan_value > 0:
                self.enable_pin.set_digital(print_time, 0)
        if (fan_speed and fan_speed < self.max_power and self.kick_start_time
            and (not self.last_fan_value or fan_speed - self.last_fan_value > .5)):
            # Run fan at full speed for specified kick_start_time
            self.mcu_fan.set_pwm(print_time, self.max_power)
            print_time += self.kick_start_time
        self.mcu_fan.set_pwm(print_time, fan_speed)
        self.last_fan_time = print_time
        #Leave last_fan_speed as value so UI doesn't see the scaling
        self.last_fan_value = value
    def set_speed_from_command(self, value):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.register_lookahead_callback((lambda pt:
                                              self.set_speed(pt, value)))
    def _handle_request_restart(self, print_time):
        self.set_speed(print_time, 0.)

    def get_status(self, eventtime):
        tachometer_status = self.tachometer.get_status(eventtime)
        return {
            'speed': self.last_fan_value,
            'rpm': tachometer_status['rpm'],
        }

class FanTachometer:
    def __init__(self, config, fan):
        self.printer = config.get_printer()
        self.config = config
        self.fan = fan
        self.ppr = self.poll_time = self._freq_counter = None
        self.tach_loss_count = self.tach_loss_interval = None
        self.warning_repeat_interval = None
        self.tach_loss_action = lambda _: None
        self.initialize_frequency_counter(config)
        self.fan_name = config.get_name().split()[-1]
        self.tach_loss_time = None
        self.last_warning_time = 0
        self.warning_issued = False


    def handle_connect(self):
        fan = self.printer.lookup_object(' '.join(self.fan.fan_name))
        heater_names = getattr(fan, 'heater_names', [])
        if len(heater_names) > 0:
            if not self.tach_loss_action == self.shutdown:
                raise self.printer.config_error(f"{self.fan_name} controls"
                    f" a heater so must have a tach_loss_action of 'shutdown'")

    def initialize_frequency_counter(self, config):
        pin = self.config.get('tachometer_pin', None)
        if pin:
            sample_time = 1.0
            self.poll_time = self.config.getfloat('tachometer_poll_interval'
                                                  , 0.0015, above=0.)
            self.ppr = self.config.getint('tachometer_ppr', 2, minval=1)
            self._freq_counter = pulse_counter.FrequencyCounter(
                self.printer, pin, sample_time, self.poll_time)
            #Only setup fail options if a valid tach fan
            self.initialize_tach_fail_options(config)
            self.printer.register_event_handler("klippy:connect", self.handle_connect)
        else:
            self._freq_counter = None

    def initialize_tach_fail_options(self,config):
        self.tach_loss_interval = (
        config.getfloat('tach_loss_interval',
                        default=3, above=0., below=10.))
        action =  {'shutdown': self.shutdown, 'warning': self.warning,
                   'none': lambda _: None}

        self.tach_loss_action = (
            self.config.getchoice('tach_loss_action', action,
                              default='shutdown'))
        self.warning_repeat_interval = (
            config.getfloat('tach_warning_repeat_interval', above=-1,
                        default=self.tach_loss_interval))

    def shutdown(self,eventtime):
        self.printer.invoke_shutdown(
            f"Tach signal lost on {self.fan_name} for longer than"
            f" {self.tach_loss_interval} seconds.")

    def warning(self, eventtime):
        if self.warning_repeat_interval == 0 and self.warning_issued:
            return  # Do not issue the warning again

        interval = eventtime - self.last_warning_time
        if (not self.last_warning_time or
                interval >= self.warning_repeat_interval):
            warning = f"!! Warning: {self.fan_name} has lost tach signal"
            warning += f" for longer than {self.tach_loss_interval} seconds!"
            self.printer.lookup_object('gcode').respond_raw(warning)
            self.last_warning_time = eventtime

    def get_status(self, eventtime):
        if self._freq_counter is not None:
            rpm = self._freq_counter.get_frequency() * 30. / self.ppr
            #Reset the tach loss time if we get a tach signal again
            if rpm > 0 and self.tach_loss_time:
                self.tach_loss_time = None
            if rpm == 0.0 and self.fan.last_fan_value > 0:
                #Hold the initial time of tach signal loss
                if not self.tach_loss_time:
                    self.tach_loss_time = eventtime
                elif eventtime - self.tach_loss_time > self.tach_loss_interval:
                    self.tach_loss_action(eventtime)
        else:
            rpm = None
        return {'rpm': rpm}

class PrinterFan:
    def __init__(self, config):
        self.fan = Fan(config)
        self.printer = config.get_printer()
        self.fan_list = {0: self.fan}
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        self.fan_number = 0

        if not "M106" in self.gcode.ready_gcode_handlers:
            self.gcode.register_command("M106", self.cmd_M106)
        if not "M106" in self.gcode.ready_gcode_handlers:
            self.gcode.register_command("M107", self.cmd_M107)
    def get_status(self, eventtime):
        return self.fan.get_status(eventtime)
    def cmd_M106(self, gcmd):
        # Set fan speed
        value = gcmd.get_float('S', 255., minval=0.)

        fan_number = gcmd.get_int('T', default=0)

        if fan_number not in self.fan_list.keys():
            self.gcode.respond_raw(f"!! T{fan_number} is an invalid fan number")
        else:
            fan = self.fan_list[fan_number]
            #Future proofs against Slicer changes to 0 -> 1 fan speed
            #Currently accepted in RepRap Standard
            if 0 < value < 1:
                fan.set_speed_from_command(value)
            #Traditional M106 command with speed from 0 -> 255
            else:
                value = gcmd.get_float('S', 255., minval=0.) / 255.
            fan.set_speed_from_command(value)
    def cmd_M107(self, gcmd):
        # Turn fan off
        fan_number = gcmd.get_int('T', default=0)

        if fan_number not in self.fan_list.keys():
            self.gcode.respond_raw(f"!! T{fan_number} is an invalid fan number")
        else:
            fan = self.fan_list[fan_number]
            fan.set_speed_from_command(0.)

    def add_fan(self, fan_number, Fan):
        if fan_number in self.fan_list.keys():
            if fan_number == 0:
                error_message = ("Slicer fan number cannot be 0.\n"
                                 "Slicer fan 0 is defined by [fan] config.")
                raise self.printer.config_error(error_message)
            else:
                error_message = f"Slicer fan number {fan_number} is already defined"
                raise self.printer.config_error(error_message)
        else:
            self.fan_list[fan_number] = Fan


def load_config(config):
    return PrinterFan(config)

def load_config_prefix(config):
    return PrinterFan(config)