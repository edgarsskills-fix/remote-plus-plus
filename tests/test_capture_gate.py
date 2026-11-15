from pathlib import Path
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture_gate import prepare
from core import RemoteError

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('cc') and shutil.which('pkg-config') and shutil.which('dbus-run-session'),
                     'native build tools are required')
class NativeGate(unittest.TestCase):
    def test_unknown_google_binary_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'libremoting_core.so').write_bytes(b'unreviewed host')
            with self.assertRaisesRegex(RemoteError, 'не проверена'):
                prepare(ROOT, root / 'host')

    def test_capture_is_deferred_and_multiple_clients_are_counted(self):
        flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'gio-2.0'], text=True))
        with tempfile.TemporaryDirectory() as folder:
            gate = Path(folder) / 'gate.so'
            fixture = Path(folder) / 'fixture'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                            '-o', str(gate), str(ROOT / 'native/capture_gate.c'), *flags, '-ldl'], check=True)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-o', str(fixture),
                            str(ROOT / 'tests/capture_gate_fixture.c'), *flags], check=True)
            parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
            env = os.environ.copy()
            env.pop('LD_PRELOAD', None)
            # env applies LD_PRELOAD to the fixture only, not dbus-daemon.
            process = subprocess.Popen(['dbus-run-session', '--', 'env', 'LD_PRELOAD=' + str(gate),
                                        'REMOTEPLUS_GATE_FD=' + str(child.fileno()), str(fixture)],
                                       env=env, pass_fds=(child.fileno(),), stderr=subprocess.PIPE)
            child.close()
            with parent:
                result = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, result[1].decode())
                parent.settimeout(0.05)
                events = []
                while True:
                    try:
                        events.append(parent.recv(128).decode())
                    except socket.timeout:
                        break
            self.assertEqual(events, ['ready', 'held', 'held', 'active', 'released', 'released', 'active', 'active', 'idle'])

    def test_supervisor_recycles_after_disconnect_and_stops_its_child(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stub = root / 'empty.c'
            stub.write_text('int remoteplus_test_stub;\n')
            gate = root / 'stub.so'
            subprocess.run(['cc', '-shared', '-fPIC', '-o', str(gate), str(stub)], check=True)
            host = root / 'host'
            host.write_text('''#!/usr/bin/python3
import os, signal, socket, sys, time
from pathlib import Path
root=Path(__file__).resolve().parent
events=socket.socket(fileno=int(os.environ['REMOTEPLUS_GATE_FD']))
events.send(b'ready')
marker=root/'first.pid'
if not marker.exists():
 marker.write_text(str(os.getpid()))
 events.send(b'active'); events.send(b'idle')
 time.sleep(.1); events.send(b'active')
 time.sleep(.6)
 (root/'reconnect-survived').write_text('yes')
 events.send(b'idle')
else:
 (root/'standby.pid').write_text(str(os.getpid()))
while True: time.sleep(.05)
''')
            host.chmod(0o755)
            controller = subprocess.Popen([sys.executable, '-c',
                'from pathlib import Path; from capture_gate import supervise; '
                'import sys; sys.exit(supervise(*map(Path,sys.argv[1:])))',
                str(host), str(root / 'unused.json'), str(gate)], cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            pids = []
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and not (root / 'standby.pid').exists():
                    if controller.poll() is not None:
                        self.fail(controller.communicate()[1].decode())
                    time.sleep(.05)
                self.assertTrue((root / 'standby.pid').exists(), 'Host was not recycled')
                self.assertTrue((root / 'reconnect-survived').exists(), 'A reconnect must cancel idle shutdown')
                pids = [int((root / name).read_text()) for name in ('first.pid', 'standby.pid')]
                self.assertNotEqual(*pids)
                controller.terminate()
                output, error = controller.communicate(timeout=5)
                self.assertEqual(controller.returncode, 0, error.decode())
                self.assertIn(b'last client disconnected', output)
                for pid in pids:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
            finally:
                if controller.poll() is None:
                    controller.kill(); controller.communicate()
                for name in ('first.pid', 'standby.pid'):
                    if (root / name).exists():
                        try: os.kill(int((root / name).read_text()), 9)
                        except ProcessLookupError: pass


if __name__ == '__main__':
    unittest.main()
