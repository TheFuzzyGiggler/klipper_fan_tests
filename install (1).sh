#!/bin/sh
# Automatically install the Klipper K1 Heater Test
#
# Copyright (C) 2024 TheFuzzyGiggler <github.com/TheFuzzyGiggler>
#
# This file may be distributed under the terms of the GNU GPLv3 License.

# Force script to exit if an error occurs
set -e

KLIPPER_PATH="${HOME}/klipper"
SYSTEMDDIR="/etc/systemd/system"
SRCDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/ && pwd )"

# Verify we're not running as root
if [ "$(id -u)" -eq 0 ]; then
    echo "This script must not run as root"
    exit -1
fi

# Check if Klipper is installed
if [ "$(sudo systemctl list-units --full -all -t service --no-legend | grep -F "klipper.service")" ]; then
    echo "Klipper service found!"
else
    echo "Klipper service not found, please install Klipper first"
    exit -1
fi

# Check command line argument
ACTION="${1:-install}"  # Default to "install" if no argument is provided


case "$ACTION" in
    install)
        echo "Installing..."
        # Backup existing heater_bed.py if it exists
        if [ -f "${KLIPPER_PATH}/klippy/extras/heater_bed.py" ]; then
            echo "Backing up existing heater_bed.py to heater_bed.py.bak..."
            cp -f "${KLIPPER_PATH}/klippy/extras/heater_bed.py" "${KLIPPER_PATH}/klippy/extras/heater_bed.py.bak"
        fi
        # Link heater_bed.py to klipper
        echo "Linking heater patch to Klipper..."
        ln -sf "${SRCDIR}/heater_bed.py" "${KLIPPER_PATH}/klippy/extras/heater_bed.py"

        # Restart klipper
        echo "Restarting Klipper..."
        sudo systemctl restart klipper
        ;;

    uninstall)
        echo "Uninstalling..."
        # Remove heater_bed.py
        echo "Removing heater patch from Klipper..."
        rm -f "${KLIPPER_PATH}/klippy/extras/heater_bed.py"

        # Check if backup exists and restore it
        if [ -f "${KLIPPER_PATH}/klippy/extras/heater_bed.py.bak" ]; then
            echo "Restoring original heater_bed.py from backup..."
            mv -f "${KLIPPER_PATH}/klippy/extras/heater_bed.py.bak" "${KLIPPER_PATH}/klippy/extras/heater_bed.py"
        fi

        # Restart klipper
        echo "Restarting Klipper..."
        sudo systemctl restart klipper
        ;;

    *)
        echo "Invalid action: $ACTION"
        echo "Usage: $0 [install|uninstall]"
        exit 1
        ;;
esac

echo "Operation $ACTION completed successfully."
