from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import current_session
from core import RemoteError, UNIT


class AutomaticUpdate(unittest.TestCase):
    @patch('current_session.registered', return_value=False)
    @patch('current_session.read_config', return_value={})
    @patch('current_session.run')
    def test_fresh_install_does_not_enable_access(self, run, *_):
        current_session.apply_update(Path('/app'))
        run.assert_not_called()

    @patch('current_session.Backend')
    @patch('current_session.registered', return_value=True)
    @patch('current_session.read_config', return_value={})
    @patch('current_session.prepare')
    @patch('current_session.run')
    def test_existing_service_is_upgraded_preserving_enabled_setting(self, run, prepare, _, __, backend):
        backend.return_value.compatibility.return_value = (True, '')
        for active in (True, False):
            with self.subTest(active=active):
                run.reset_mock(); prepare.reset_mock(); backend.reset_mock()
                run.return_value = subprocess.CompletedProcess([], 0, 'LoadState=loaded\nActiveState=' + ('active' if active else 'inactive'))
                current_session.apply_update(Path('/app'))
                prepare.assert_called_once()
                backend.return_value.install_integration.assert_called_once()
                backend.return_value.set_autostart.assert_not_called()
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(['systemctl', '--user', 'restart', UNIT] in commands, active)

    @patch('current_session.Backend')
    @patch('current_session.registered', return_value=True)
    @patch('current_session.read_config', return_value={})
    @patch('current_session.prepare', side_effect=RemoteError('unreviewed'))
    @patch('current_session.run')
    def test_unreviewed_host_does_not_replace_live_integration(self, run, prepare, _, __, backend):
        backend.return_value.compatibility.return_value = (True, '')
        run.return_value = subprocess.CompletedProcess([], 0, 'LoadState=loaded\nActiveState=active')
        with self.assertRaises(RemoteError):
            current_session.apply_update(Path('/app'))
        backend.return_value.install_integration.assert_not_called()
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
