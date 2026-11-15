#!/usr/bin/python3
"""Attach Google's host to the current GNOME Wayland session."""
import os
import sys
from core import HOST, config_path, read_config, registered


def main():
    if os.environ.get('XDG_SESSION_TYPE') != 'wayland':
        sys.exit('Remote++ requires the current Wayland session.')
    if 'GNOME' not in os.environ.get('XDG_CURRENT_DESKTOP', '').split(':'):
        sys.exit('Remote++ currently supports GNOME.')
    if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        sys.exit('No graphical session D-Bus address.')
    if not registered(read_config()):
        sys.exit('Register this computer in Remote++ first.')
    if not HOST.is_file():
        sys.exit('Install the Google host component in Remote++.')
    if '--check' in sys.argv:
        print('Current session and registration are available.')
        return
    os.execv(str(HOST), [str(HOST), '--host-config=' + str(config_path())])


if __name__ == '__main__':
    main()
