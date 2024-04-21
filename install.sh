#!/bin/sh
# Automatically install the Klipper Fan Tests
#
# Copyright (C) 2024 TheFuzzyGiggler <github.com/TheFuzzyGiggler>
#
# This file may be distributed under the terms of the GNU GPLv3 License.

# Force script to exit if an error occurs
set -e

# Define paths
KLIPPER_PATH="${HOME}/klipper"
SRCDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/ && pwd )"

# Define files to be managed
files_to_manage="fan.py fan_generic.py temperature_fan.py"

# Check if running as root
if [ "$(id -u)" -eq 0 ]; then
    echo "This script must not run as root"
    exit -1
fi

# Check if Klipper is installed
if ! systemctl is-active --quiet klipper; then
    echo "Klipper service not found, please install Klipper first"
    exit -1
fi

# Determine action
ACTION="${1:-install}"

case "$ACTION" in
    install)
        echo "Installing..."
        for file in $files_to_manage; do
            if [ -f "${KLIPPER_PATH}/klippy/extras/${file}" ]; then
                echo "Backing up existing ${file} to ${file}.bak..."
                cp -f "${KLIPPER_PATH}/klippy/extras/${file}" "${KLIPPER_PATH}/klippy/extras/${file}.bak"
            fi
            echo "Linking ${file} to Klipper directory..."
            cp -f "${SRCDIR}/${file}" "${KLIPPER_PATH}/klippy/extras/${file}"
        done
        echo "Restarting Klipper..."
        sudo systemctl restart klipper
        ;;
    uninstall)
        echo "Uninstalling..."
        for file in $files_to_manage; do
            if [ -f "${KLIPPER_PATH}/klippy/extras/${file}.bak" ]; then
                echo "Restoring original ${file} from backup..."
                mv -f "${KLIPPER_PATH}/klippy/extras/${file}.bak" "${KLIPPER_PATH}/klippy/extras/${file}"
            else
                echo "Removing ${file}..."
                rm -f "${KLIPPER_PATH}/klippy/extras/${file}"
            fi
        done
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
