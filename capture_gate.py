"""Build and supervise the per-host native Mutter capture gate."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import selectors
import shlex
import signal
import socket
import subprocess
import tempfile
import time

from core import RemoteError

# This adapter relies on HostEventLoggerPosix event semantics. Refuse a new
# Google binary until its authentication/lifecycle behavior has been audited.
SUPPORTED_CORE_SHA256 = 'a5088ff991395e5877216b48bc54f488927382daf29f734668c8efc996a59d7c'


def prepare(base: Path, host: Path) -> Path:
    library = host.with_name('libremoting_core.so')
    if hashlib.sha256(library.read_bytes()).hexdigest() != SUPPORTED_CORE_SHA256:
        raise RemoteError('Эта версия Google-хоста ещё не проверена для захвата по подключению. '
                          'Обновите Remote++; доступ оставлен выключенным.')
    source = base / 'native/capture_gate.c'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'remote-plus-plus/native'
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = cache / ('capture-gate-' + digest + '.so')
    if target.is_file():
        return target
    try:
        flags = subprocess.check_output(['pkg-config', '--cflags', '--libs', 'gio-2.0'], text=True)
        with tempfile.TemporaryDirectory(prefix='build-', dir=cache) as tmp:
            output = Path(tmp) / 'gate.so'
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-shared', '-fPIC', '-Wl,-z,relro,-z,now', '-o', str(output),
                            str(source), *shlex.split(flags), '-ldl'], check=True,
                           capture_output=True, text=True)
            output.chmod(0o700)
            output.replace(target)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RemoteError('Не удалось собрать модуль захвата. Установите gcc, pkgconf и glib2, '
                          'затем повторите запуск.') from exc
    return target


def supervise(host: Path, config: Path, gate: Path) -> int:
    stopping = False
    child = None

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.send_signal(signum)

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        while not stopping:
            parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
            env = os.environ.copy()
            # Apply the module only to this child; no desktop-wide preload.
            env['LD_PRELOAD'] = str(gate)
            env['REMOTEPLUS_GATE_FD'] = str(worker.fileno())
            child = subprocess.Popen([str(host), '--host-config=' + str(config)],
                                     env=env, pass_fds=(worker.fileno(),))
            worker.close()
            idle_deadline = None
            ready = False
            start = time.monotonic()
            recycled = False
            with parent, selectors.DefaultSelector() as selector:
                selector.register(parent, selectors.EVENT_READ)
                while child.poll() is None:
                    for key, _ in selector.select(0.1):
                        event = key.fileobj.recv(128).decode('ascii', errors='replace')
                        if event == 'ready':
                            ready = True
                            print('Remote++: capture gate loaded.', flush=True)
                        elif event == 'held':
                            print('Remote++: waiting for an authenticated client; capture is not started.', flush=True)
                        elif event == 'active':
                            idle_deadline = None
                        elif event == 'idle':
                            idle_deadline = time.monotonic() + 0.35
                        elif event == 'released':
                            print('Remote++: authenticated client; starting GNOME capture.', flush=True)
                    now = time.monotonic()
                    if not ready and now - start > 10:
                        child.terminate()
                        raise RemoteError('Модуль захвата не загрузился. Хост остановлен.')
                    if idle_deadline is not None and now >= idle_deadline and not stopping:
                        print('Remote++: last client disconnected; releasing capture and returning to standby.', flush=True)
                        recycled = True
                        child.terminate()
                        break
                try:
                    result = child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    result = child.wait()
            if stopping:
                return 0
            if not recycled:
                return result if result > 0 else 1
            time.sleep(0.2)
        return 0
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
