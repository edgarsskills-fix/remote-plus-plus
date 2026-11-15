import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core


class CommandParsing(unittest.TestCase):
    code = '4/abcdefghijk_1234567890'

    def command(self, name='$(hostname)'):
        return f'DISPLAY= {core.START_HOST} --code="{self.code}" --redirect-url="{core.REDIRECT}" --name={name}'

    def test_real_google_format(self):
        self.assertEqual(core.parse_google_command(self.command()), self.code)
        self.assertEqual(core.parse_google_command(self.command().replace('DISPLAY= ', '')), self.code)

    def test_command_substitutions_are_never_evaluated(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'owned'
            # Even shell-looking values are data and the display name is discarded.
            text = self.command('$(id)')
            with patch('subprocess.run', side_effect=AssertionError('Must not execute')):
                self.assertEqual(core.parse_google_command(text), self.code)
            self.assertFalse(marker.exists())

    def test_reject_shell_and_foreign_arguments(self):
        variants = [self.command() + '; touch /tmp/owned', self.command() + ' && id',
                    self.command() + ' --code=anothercode12345',
                    self.command().replace(core.REDIRECT, 'https://evil.example/oauth'),
                    self.command().replace(str(core.START_HOST), '/bin/sh'),
                    self.command() + ' --host-type=gdm-managed',
                    self.command() + '\nid', '', 'x' * 12001,
                    self.command().replace(self.code, 'bad;value')]
        for text in variants:
            with self.subTest(text=text):
                with self.assertRaises(core.RemoteError):
                    core.parse_google_command(text)


class Credentials(unittest.TestCase):
    def test_pin_rules(self):
        for pin, confirmation in [('12345', '12345'), ('12abc6', '12abc6'),
                                   ('１２３４５６', '１２３４５６'), ('123456', '234567'),
                                   ('1' * 21, '1' * 21)]:
            with self.assertRaises(core.RemoteError):
                core.validate_pin(pin, confirmation)
        core.validate_pin('012345', '012345')

    def test_hash_matches_openssl(self):
        # Independent implementation, including Google's non-obvious argument order.
        result = subprocess.run(['openssl', 'dgst', '-sha256', '-hmac', 'test-host-id', '-binary'],
                                input=b'123456', capture_output=True, check=True)
        expected = 'hmac:' + base64.b64encode(result.stdout).decode()
        self.assertEqual(core.make_pin_hash('test-host-id', '123456'), expected)

    def test_pin_update_preserves_registration_and_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'host.json'
            original = {'host_id': 'test-host-id', 'private_key': 'PRIVATE-TEST-KEY',
                        'oauth_refresh_token': 'REFRESH-TEST-TOKEN', 'host_secret_hash': 'old-hash',
                        'host_name': 'My PC', 'custom_field': {'keep': True}}
            p.write_text(json.dumps(original))
            p.chmod(0o644)
            core.replace_pin(p, '123456', '123456')
            data = json.loads(p.read_text())
            for key in original.keys() - {'host_secret_hash'}:
                self.assertEqual(data[key], original[key])
            self.assertEqual(data['host_secret_hash'], core.make_pin_hash('test-host-id', '123456'))
            self.assertNotIn('123456', p.read_text())
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(list(Path(tmp).iterdir()), [p])

    def test_invalid_pin_does_not_change_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'host.json'
            p.write_text('{"host_id":"test"}')
            before = p.read_bytes()
            with self.assertRaises(core.RemoteError):
                core.replace_pin(p, '123456', '654321')
            self.assertEqual(p.read_bytes(), before)

    def test_diagnostics_redact_secrets(self):
        source = 'account@example.com code=4/abcdefghijklmnopqrstuvwxyz --pin=123456 private_key: TOPSECRET'
        text = core.sanitize(source, ('123456',))
        for secret in ('account@example.com', '4/abcdefghijklmnopqrstuvwxyz', '123456', 'TOPSECRET'):
            self.assertNotIn(secret, text)


class Lifecycle(unittest.TestCase):
    def environment(self):
        return {'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'GNOME',
                'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/test'}

    @patch('core.run')
    @patch('core.shutil.which', return_value='/usr/bin/pacman')
    def test_service_quotes_path_and_stays_per_user(self, _, run):
        with tempfile.TemporaryDirectory(prefix='remote test % ') as tmp:
            root = Path(tmp)
            app = root / 'app'
            (app / 'data').mkdir(parents=True)
            (app / 'current_session.py').write_text('# test')
            (app / 'data/crd-current-session.service.in').write_text((core.BASE / 'data/crd-current-session.service.in').read_text())
            backend = core.Backend(home=root / 'user', base=app, environ=self.environment())
            backend.install_integration()
            unit = backend.unit_dir / core.UNIT
            self.assertTrue(unit.exists())
            text = unit.read_text()
            self.assertIn('%%', text)
            self.assertIn('ExecStart=/usr/bin/python3 "', text)
            self.assertNotIn('sudo', text)
            run.assert_called_once_with(['systemctl', '--user', 'daemon-reload'])

    @patch('core.run')
    def test_disconnect_retains_recoverable_private_registration(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            backend = core.Backend(home=Path(tmp), environ=self.environment())
            backend.config.parent.mkdir(parents=True)
            backend.config.write_text('{"test":"registration"}')
            backend.disconnect()
            self.assertFalse(backend.config.exists())
            backups = list(backend.config.parent.glob('*.disconnected-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text()), {'test': 'registration'})
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)

    @patch('core.run')
    def test_unsupported_session_cannot_register(self, run):
        backend = core.Backend(environ={'XDG_SESSION_TYPE': 'x11'})
        with self.assertRaises(core.RemoteError):
            backend.connect_google('ignored', 'Test PC', '123456', '123456')
        run.assert_not_called()

    @patch('core.HOST')
    @patch('core.run')
    @patch.object(core.Backend, 'require_compatible')
    @patch.object(core.Backend, '_check_legacy')
    @patch.object(core.Backend, 'start')
    def test_registration_uses_argv_no_start_and_redacts_oauth(self, start, legacy, compatible, run, host):
        host.is_file.return_value = True
        with tempfile.TemporaryDirectory() as tmp:
            backend = core.Backend(home=Path(tmp), environ=self.environment())
            code = CommandParsing.code
            def register(argv, **kwargs):
                self.assertIn('--no-start', argv)
                self.assertIn('--host-type=single-process', argv)
                self.assertIn('--pin=123456', argv)
                self.assertEqual(kwargs['env']['DISPLAY'], '')
                self.assertEqual(kwargs['secrets'], (code, '123456'))
                self.assertNotIn('shell', kwargs)
                core.atomic_private_write(backend.config, json.dumps({'host_id': 'test', 'private_key': 'key',
                    'oauth_refresh_token': 'token', 'host_secret_hash': 'hash'}))
            run.side_effect = register
            backend.connect_google(CommandParsing().command(), 'Test PC', '123456', '123456')
            start.assert_called_once_with(True)

    @patch('core.HOST')
    @patch('core.run')
    @patch.object(core.Backend, 'require_compatible')
    @patch.object(core.Backend, '_check_legacy')
    @patch.object(core.Backend, 'start')
    def test_expired_oauth_does_not_start_service(self, start, legacy, compatible, run, host):
        host.is_file.return_value = True
        run.side_effect = core.RemoteError('OAuth invalid_grant')
        with tempfile.TemporaryDirectory() as tmp:
            backend = core.Backend(home=Path(tmp), environ=self.environment())
            with self.assertRaisesRegex(core.RemoteError, 'истёк'):
                backend.connect_google(CommandParsing().command(), 'Test PC', '123456', '123456')
            self.assertFalse(backend.config.exists())
            start.assert_not_called()

    def test_published_tree_has_no_host_registration(self):
        for p in core.BASE.rglob('*'):
            if '.git' in p.parts or '__pycache__' in p.parts:
                continue
            self.assertFalse(p.name.startswith('host#'), str(p))
            self.assertNotIn('credentials', p.name.lower())


if __name__ == '__main__':
    unittest.main()
