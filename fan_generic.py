# Support fans that are controlled by gcode
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from . import fan

class PrinterFanGeneric:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.fan = fan.Fan(config, default_shutdown_speed=0.)
        self.fan_name = config.get_name().split()[-1]

    def get_status(self, eventtime):
        return self.fan.get_status(eventtime)

def load_config_prefix(config):
    return PrinterFanGeneric(config)
