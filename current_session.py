#!/usr/bin/python3
"""Attach Google's host to the current GNOME Wayland session."""
import os
from pathlib import Path
import sys
from core import HOST, UNIT, Backend, RemoteError, config_path, read_config, registered, run
from capture_gate import prepare, supervise


def apply_update(base: Path):
    """Upgrade an existing integration while retaining its on/off state."""
    backend = Backend(base=base)
    if not registered(read_config()) or not backend.compatibility()[0]:
        return
    result = run(['systemctl', '--user', 'show', UNIT,
                  '--property=LoadState,ActiveState'], check=False)
    state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if state.get('LoadState') != 'loaded':
        return
    # Verify/build before replacing the running integration. No unreviewed
    # Google version is silently switched to the old eager-capture behavior.
    prepare(base, HOST)
    backend.install_integration()
    if state.get('ActiveState') in ('active', 'activating', 'reloading'):
        print('Применение захвата по подключению: перезапуск службы удалённого доступа…', flush=True)
        run(['systemctl', '--user', 'restart', UNIT])
    else:
        print('Модуль захвата обновлён. Доступ остаётся выключенным.', flush=True)


def main():
    base = Path(__file__).resolve().parent
    if '--apply-update' in sys.argv:
        apply_update(base)
        return
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
    gate = prepare(base, HOST)
    sys.exit(supervise(HOST, config_path(), gate))


if __name__ == '__main__':
    try:
        main()
    except RemoteError as exc:
        sys.exit(str(exc))
