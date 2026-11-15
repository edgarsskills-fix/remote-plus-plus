#!/usr/bin/python3
"""Install Remote++ for the current user; never run the GUI as root."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
APP_ID = 'io.github.edgarsskills.RemotePlusPlus'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-launch', action='store_true')
    options = parser.parse_args()
    if os.geteuid() == 0:
        sys.exit('Запустите установщик без sudo. Права администратора будут запрошены отдельно при необходимости.')
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Adw', '1')
        from gi.repository import Gtk, Adw  # noqa: F401
    except (ImportError, ValueError):
        print('Установка GTK4 / libadwaita: подтвердите запрос прав администратора.', flush=True)
        subprocess.run(['pkexec', 'pacman', '-S', '--needed', '--noconfirm', 'python-gobject', 'gtk4', 'libadwaita'], check=True)
    if not shutil.which('cc') or not shutil.which('pkg-config') or subprocess.run(
            ['pkg-config', '--exists', 'gio-2.0'], check=False).returncode:
        print('Установка средств сборки модуля захвата: подтвердите запрос прав администратора.', flush=True)
        subprocess.run(['pkexec', 'pacman', '-S', '--needed', '--noconfirm', 'gcc', 'pkgconf', 'glib2'], check=True)
    home = Path.home()
    data = Path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share')))
    binary = home / '.local/bin'
    target = data / 'remote-plus-plus'
    data.mkdir(parents=True, exist_ok=True)
    if HERE != target:
        with tempfile.TemporaryDirectory(prefix='.remote-plus-install-', dir=data) as tmp:
            stage = Path(tmp) / 'app'
            stage.mkdir()
            for name in ('app.py', 'core.py', 'current_session.py', 'capture_gate.py', 'install.py', 'README.md', 'LICENSE', 'THIRD_PARTY.md'):
                shutil.copy2(HERE / name, stage / name)
            shutil.copytree(HERE / 'data', stage / 'data')
            shutil.copytree(HERE / 'native', stage / 'native')
            shutil.copytree(HERE / 'packaging/host', stage / 'packaging/host')
            old = Path(tmp) / 'previous'
            if target.exists():
                target.rename(old)
            try:
                stage.rename(target)
            except OSError:
                if old.exists():
                    old.rename(target)
                raise
    binary.mkdir(parents=True, exist_ok=True)
    launcher = binary / 'remote-plus-plus'
    # exec argv through a Python launcher avoids shell quoting and path spaces.
    launcher.write_text('#!/usr/bin/python3\nimport os\nos.execv("/usr/bin/python3", ["/usr/bin/python3", ' + repr(str(target / 'app.py')) + '])\n')
    launcher.chmod(0o755)
    for size in ('scalable',):
        icons = data / 'icons/hicolor' / size / 'apps'
        icons.mkdir(parents=True, exist_ok=True)
        for name in (APP_ID + '.svg', APP_ID + '-symbolic.svg'):
            shutil.copy2(target / 'data' / name, icons / name)
    apps = data / 'applications'
    apps.mkdir(parents=True, exist_ok=True)
    escaped = str(launcher).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    (apps / (APP_ID + '.desktop')).write_text(
        '[Desktop Entry]\nType=Application\nName=Remote++\n'
        'GenericName=Remote Desktop Setup\nGenericName[ru]=Настройка удалённого доступа\n'
        'Comment=Set up Chrome Remote Desktop for the current GNOME session\n'
        'Comment[ru]=Удалённый доступ к текущему рабочему столу через Google\n'
        f'Exec="{escaped}"\nIcon={APP_ID}\nTerminal=false\nCategories=Network;RemoteAccess;GTK;\n'
        f'StartupNotify=true\nStartupWMClass={APP_ID}\n')
    for command in (['update-desktop-database', str(apps)], ['gtk-update-icon-cache', '-f', '-t', str(data / 'icons/hicolor')]):
        if shutil.which(command[0]):
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['/usr/bin/python3', str(target / 'current_session.py'), '--apply-update'], check=True)
    print('Remote++ установлен. Откройте его в меню приложений.', flush=True)
    if not options.no_launch:
        subprocess.Popen(['/usr/bin/python3', str(target / 'app.py')], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
