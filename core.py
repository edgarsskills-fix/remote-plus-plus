"""Remote++ backend. No GTK, shell execution, or plaintext PIN storage."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Callable

VERSION = '0.2.0'
APP_ID = 'io.github.edgarsskills.RemotePlusPlus'
UNIT = 'crd-current-session.service'
GOOGLE_SETUP = 'https://remotedesktop.google.com/headless'
GOOGLE_ACCESS = 'https://remotedesktop.google.com/access'
REDIRECT = 'https://remotedesktop.google.com/_/oauthredirect'
HOST = Path('/opt/google/chrome-remote-desktop/chrome-remote-desktop-host')
START_HOST = HOST.with_name('start-host')
BASE = Path(__file__).resolve().parent


class RemoteError(Exception):
    """An actionable, safe-to-display error."""


def config_path(home: Path | None = None, hostname: str | None = None) -> Path:
    home = home if home is not None else Path.home()
    digest = hashlib.md5((hostname or socket.gethostname()).encode()).hexdigest()
    return home / '.config/chrome-remote-desktop' / f'host#{digest}.json'


def read_config(path: Path | None = None) -> dict:
    path = path if path is not None else config_path()
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        raise RemoteError('Не удалось прочитать регистрацию Google. Проверьте права на файл конфигурации.') from exc


def registered(data: dict) -> bool:
    return all(data.get(k) for k in ('host_id', 'private_key', 'oauth_refresh_token', 'host_secret_hash'))


def atomic_private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.remote-plus-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_pin(pin: str, confirmation: str) -> None:
    if not re.fullmatch(r'[0-9]{6,20}', pin):
        raise RemoteError('PIN должен содержать от 6 до 20 цифр.')
    if pin != confirmation:
        raise RemoteError('Введённые PIN не совпадают.')


def make_pin_hash(host_id: str, pin: str) -> str:
    digest = hmac.new(host_id.encode(), pin.encode(), hashlib.sha256).digest()
    return 'hmac:' + base64.b64encode(digest).decode()


def replace_pin(path: Path, pin: str, confirmation: str) -> None:
    validate_pin(pin, confirmation)
    data = read_config(path)
    if not registered(data):
        raise RemoteError('Сначала привяжите компьютер к аккаунту Google.')
    data['host_secret_hash'] = make_pin_hash(data['host_id'], pin)
    atomic_private_write(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def parse_google_command(command: str) -> str:
    """Extract OAuth code as data; never evaluate the pasted shell command."""
    if not command.strip() or len(command) > 12000:
        raise RemoteError('Вставьте команду для Debian Linux со страницы Google.')
    try:
        words = shlex.split(command.strip())
    except ValueError as exc:
        raise RemoteError('Команда скопирована не полностью. Скопируйте её ещё раз.') from exc
    if words and words[0] == 'DISPLAY=':
        words.pop(0)
    if not words or words.pop(0) != str(START_HOST):
        raise RemoteError('Нужна команда Debian Linux, начинающаяся с /opt/google/chrome-remote-desktop/start-host.')
    values = {}
    allowed = {'code', 'redirect-url', 'name', 'display-name'}
    for word in words:
        if not word.startswith('--') or '=' not in word:
            raise RemoteError('В команде есть посторонние команды или параметры. Скопируйте исходную команду Google.')
        key, value = word[2:].split('=', 1)
        if key not in allowed or key in values:
            raise RemoteError('В команде есть неизвестный или повторяющийся параметр.')
        values[key] = value
    if values.get('redirect-url') != REDIRECT:
        raise RemoteError('Адрес авторизации должен принадлежать Chrome Remote Desktop.')
    code = values.get('code', '')
    if not re.fullmatch(r'[A-Za-z0-9/_\-+.=]{10,2048}', code):
        raise RemoteError('Не удалось распознать код Google. Получите новую команду.')
    return code


def sanitize(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[скрыто]')
    text = re.sub(r'4/[A-Za-z0-9_\-+/=]{15,}', '[код Google скрыт]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[аккаунт скрыт]', text)
    text = re.sub(r'(?i)(oauth_refresh_token|private_key|host_secret_hash|access_token)["\s:=]+[^\s,}]+', r'\1: [скрыто]', text)
    return text


def run(argv: list[str], *, timeout: int = 30, check: bool = True,
        env: dict | None = None, cwd: Path | None = None,
        secrets: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        raise RemoteError('Операция не завершилась вовремя. Проверьте интернет и повторите попытку.') from exc
    except OSError as exc:
        raise RemoteError(f'Не удалось запустить {Path(argv[0]).name}.') from exc
    if check and result.returncode:
        detail = sanitize((result.stderr + '\n' + result.stdout).strip(), secrets)
        if result.returncode in (126, 127) and argv[0] == 'pkexec':
            raise RemoteError('Установка отменена в запросе прав администратора.')
        raise RemoteError(detail[-2000:] or f'Команда {Path(argv[0]).name} завершилась с ошибкой.')
    return result


@dataclass
class Status:
    compatible: bool = False
    reason: str = ''
    host_installed: bool = False
    host_version: str = ''
    registered: bool = False
    active: bool = False
    online: bool = False
    enabled: bool = False
    name: str = ''
    account: str = ''
    memory: str = '—'
    legacy_active: bool = False
    problem: str = ''


class Backend:
    def __init__(self, *, home: Path | None = None, base: Path = BASE,
                 environ: dict | None = None):
        self.home = home if home is not None else Path.home()
        self.base = base
        self.environ = dict(os.environ if environ is None else environ)
        self.config = config_path(self.home)
        self.unit_dir = Path(self.environ.get('XDG_CONFIG_HOME', str(self.home / '.config'))) / 'systemd/user'
        self._busy = False

    def compatibility(self) -> tuple[bool, str]:
        if platform.machine() != 'x86_64':
            return False, 'Нужен компьютер x86_64.'
        if self.environ.get('XDG_SESSION_TYPE') != 'wayland':
            return False, 'Войдите в графический сеанс Wayland.'
        if 'GNOME' not in self.environ.get('XDG_CURRENT_DESKTOP', '').split(':'):
            return False, 'Эта версия поддерживает GNOME на Wayland. KDE пока не поддерживается.'
        if not shutil.which('pacman'):
            return False, 'Поддерживаются CachyOS и Arch Linux.'
        if not self.environ.get('DBUS_SESSION_BUS_ADDRESS'):
            return False, 'Запустите Remote++ из меню приложений графического сеанса.'
        return True, 'CachyOS / Arch · GNOME · Wayland'

    def require_compatible(self):
        ok, why = self.compatibility()
        if not ok:
            raise RemoteError(why)

    def status(self) -> Status:
        ok, reason = self.compatibility()
        s = Status(compatible=ok, reason=reason)
        s.host_installed = HOST.is_file() and START_HOST.is_file()
        if s.host_installed:
            s.host_version = run([str(HOST), '--version'], check=False).stdout.strip()[:60]
        data = read_config(self.config)
        s.registered = registered(data)
        s.name = data.get('host_name', socket.gethostname())
        s.account = data.get('host_owner', data.get('host_owner_email', ''))
        result = run(['systemctl', '--user', 'show', UNIT, '--property=ActiveState,SubState,MainPID,MemoryCurrent'], check=False)
        state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        s.active = state.get('ActiveState') == 'active'
        enabled = run(['systemctl', '--user', 'is-enabled', UNIT], check=False).stdout.strip()
        s.enabled = enabled in ('enabled', 'enabled-runtime')
        memory = state.get('MemoryCurrent', '')
        if memory.isdigit():
            s.memory = f'{int(memory) / 1024 ** 2:.0f} МБ'
        s.legacy_active = run(['systemctl', 'is-active', f'chrome-remote-desktop@{self.home.name}.service'], check=False).returncode == 0
        if s.active and state.get('MainPID', '0') != '0':
            # Restrict the journal to this invocation; old readiness lines do not count.
            inv = run(['systemctl', '--user', 'show', UNIT, '--property=InvocationID', '--value'], check=False).stdout.strip()
            # Filtering before limiting retains connection status even after many
            # input/frame log lines have scrolled past the readiness message.
            journal = run(['journalctl', '--user', f'_SYSTEMD_INVOCATION_ID={inv}',
                r'--grep=Signaling connected\.|Host ready to receive connections\.|Signaling disconnected\.|Host is offline',
                '-n', '1', '--no-pager', '-o', 'cat'], check=False).stdout if inv else ''
            for line in journal.splitlines():
                if 'Signaling connected.' in line or 'Host ready to receive connections.' in line:
                    s.online = True
                elif 'Signaling disconnected.' in line or 'Host is offline' in line:
                    s.online = False
            error_log = run(['journalctl', '--user', f'_SYSTEMD_INVOCATION_ID={inv}',
                '--grep=ERROR:', '-n', '3', '--no-pager', '-o', 'cat'], check=False).stdout if inv and not s.online else ''
            errors = [line for line in error_log.splitlines() if 'ERROR:' in line and 'usage stats consent' not in line]
            if errors and not s.online:
                s.problem = sanitize(errors[-1])[-500:]
        return s

    def install_integration(self):
        self.require_compatible()
        launcher = self.base / 'current_session.py'
        if not launcher.is_file():
            raise RemoteError('Не найден модуль запуска. Переустановите Remote++.')
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        # systemd command-line quoting differs from shell quoting.
        quoted = '"' + str(launcher).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
        unit = (self.base / 'data/crd-current-session.service.in').read_text().replace('@LAUNCHER@', quoted)
        target = self.unit_dir / UNIT
        if target.exists() and target.read_text() != unit:
            backup = target.with_suffix('.service.remote-plus-backup')
            if not backup.exists():
                backup.write_text(target.read_text())
        target.write_text(unit)
        run(['systemctl', '--user', 'daemon-reload'])

    def install_host(self, report: Callable[[str], None] = lambda _: None):
        self.require_compatible()
        if HOST.is_file() and START_HOST.is_file():
            report('Компонент Google уже установлен.')
            return
        report('Запрос прав администратора для установки зависимостей…')
        deps = ['fakeroot', 'binutils', 'curl', 'libarchive', 'gtk3', 'nss', 'libxtst', 'libxdamage', 'libxrandr', 'mesa', 'libpipewire', 'polkit']
        run(['pkexec', 'pacman', '-S', '--needed', '--noconfirm', *deps], timeout=900)
        cache = Path(self.environ.get('XDG_CACHE_HOME', str(self.home / '.cache'))) / 'remote-plus-plus'
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='host-build-', dir=cache) as tmp:
            build = Path(tmp)
            for name in ('PKGBUILD', 'pamrule'):
                shutil.copy2(self.base / 'packaging/host' / name, build / name)
            report('Загрузка компонента с dl.google.com, проверка SHA-256 и сборка пакета…')
            run(['makepkg', '--nodeps', '--noconfirm'], timeout=900, cwd=build)
            packages = list(build.glob('remote-plus-plus-host-*.pkg.tar.zst'))
            if len(packages) != 1:
                raise RemoteError('Сборка не создала установочный пакет.')
            report('Установка проверенного пакета: подтвердите запрос администратора…')
            run(['pkexec', 'pacman', '-U', '--noconfirm', str(packages[0])], timeout=300)
        if not HOST.is_file():
            raise RemoteError('Компонент Google не появился после установки.')
        report('Компонент Google установлен.')

    def connect_google(self, command: str, name: str, pin: str, confirmation: str,
                       autostart: bool = True):
        self.require_compatible()
        validate_pin(pin, confirmation)
        code = parse_google_command(command)
        name = name.strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._\-]{0,62}', name):
            raise RemoteError('Имя компьютера: до 63 латинских букв, цифры, пробел, точка, дефис или подчёркивание.')
        if not HOST.is_file():
            raise RemoteError('Сначала установите компонент Google.')
        if registered(read_config(self.config)):
            raise RemoteError('Компьютер уже привязан. Для смены аккаунта сначала отвяжите его в разделе «Настройка».')
        self._check_legacy()
        env = self.environ.copy()
        env['DISPLAY'] = ''
        try:
            run([str(START_HOST), '--code=' + code, '--redirect-url=' + REDIRECT,
                 '--display-name=' + name, '--pin=' + pin, '--host-type=single-process',
                 '--no-start', '--disable-crash-reporting'], timeout=90, env=env,
                secrets=(code, pin))
        except RemoteError as exc:
            text = str(exc)
            if any(x in text.lower() for x in ('oauth', 'invalid_grant', 'authorization code')):
                raise RemoteError('Код Google истёк или уже использован. Получите новую команду и повторите привязку.') from exc
            raise
        if not registered(read_config(self.config)):
            raise RemoteError('Google не сохранил полную регистрацию. Повторите привязку с новым кодом.')
        self.start(autostart)

    def _check_legacy(self):
        if run(['systemctl', 'is-active', f'chrome-remote-desktop@{self.home.name}.service'], check=False).returncode == 0:
            raise RemoteError('Работает штатная служба отдельного рабочего стола. Сначала остановите её: sudo systemctl disable --now chrome-remote-desktop@' + self.home.name)

    def start(self, autostart: bool | None = None):
        self.require_compatible()
        if not registered(read_config(self.config)):
            raise RemoteError('Сначала привяжите компьютер к аккаунту Google.')
        self._check_legacy()
        from capture_gate import prepare
        prepare(self.base, HOST)
        self.install_integration()
        if autostart is not None:
            self.set_autostart(autostart)
        run(['systemctl', '--user', 'reset-failed', UNIT], check=False)
        run(['systemctl', '--user', 'start', UNIT])

    def stop(self):
        run(['systemctl', '--user', 'stop', UNIT])

    def set_autostart(self, enabled: bool):
        self.install_integration()
        run(['systemctl', '--user', 'enable', '--force', UNIT] if enabled else
            ['systemctl', '--user', 'disable', UNIT])

    def change_pin(self, pin: str, confirmation: str):
        validate_pin(pin, confirmation)
        active = run(['systemctl', '--user', 'is-active', UNIT], check=False).returncode == 0
        replace_pin(self.config, pin, confirmation)
        if active:
            run(['systemctl', '--user', 'restart', UNIT])

    def disconnect(self):
        self.stop()
        run(['systemctl', '--user', 'disable', UNIT], check=False)
        # Move registration out of the host discovery path; do not erase it.
        if self.config.exists():
            backup = self.config.with_name(self.config.name + f'.disconnected-{time.time_ns()}')
            self.config.rename(backup)
            backup.chmod(0o600)

    def diagnostics(self) -> str:
        s = self.status()
        lines = [f'Remote++ {VERSION}', f'Совместимость: {s.reason}',
                 f'Компонент Google: {s.host_version or "не установлен"}',
                 f'Регистрация: {s.registered}', f'Служба: {s.active}',
                 f'Связь с Google: {s.online}', f'Автозапуск: {s.enabled}',
                 f'Память службы: {s.memory}', '']
        log = run(['journalctl', '--user', '-u', UNIT, '-n', '80', '--no-pager', '-o', 'cat'], check=False).stdout
        lines.append(sanitize(log))
        return '\n'.join(lines)
