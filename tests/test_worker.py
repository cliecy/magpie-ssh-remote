import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('worker', ROOT / 'worker.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def profile():
    return {'id': 'ab0123456789abcd', 'host': 'test-host', 'name': 'Test',
            'localPort': 3425, 'remotePort': 13425, 'model': 'codex/gpt-6.1-sol', 'enabled': False}


class PluginTests(unittest.TestCase):
    def test_reject_injection_and_bad_ports(self):
        for field, value in [('host', '-oProxyCommand=bad'), ('host', 'host;id'),
                             ('host', 'host\nother'), ('remotePort', 0), ('localPort', True),
                             ('id', '../../anything')]:
            p = profile(); p[field] = value
            with self.assertRaises(ValueError):
                w.validate_profile(p)

    def run_script(self, script, directory):
        # Replace only the remote-home reference; never change this process's HOME.
        script = script.replace('$HOME', '$MAGPIE_TEST_REMOTE_HOME')
        env = dict(os.environ, MAGPIE_TEST_REMOTE_HOME=str(directory))
        return subprocess.run(['sh', '-s'], input=script, text=True, capture_output=True, env=env)

    def test_install_update_uninstall_real_shell(self):
        with tempfile.TemporaryDirectory(prefix='remote home ') as temporary:
            root = Path(temporary)
            p = profile()
            self.assertEqual(self.run_script(w.wrapper_script('codex', p), root).returncode, 0)
            wrapper = root / '.local/bin/codex-magpie'
            self.assertIn('wire_api=', wrapper.read_text())
            self.assertEqual(wrapper.stat().st_mode & 0o777, 0o700)
            p['model'] = 'group/default'
            self.assertEqual(self.run_script(w.wrapper_script('codex', p), root).returncode, 0)
            self.assertIn('group/default', wrapper.read_text())
            self.assertEqual(self.run_script(w.wrapper_script('codex', p, True), root).returncode, 0)
            self.assertFalse(wrapper.exists())

    def test_existing_unmanaged_file_survives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root / '.local/bin/codex-magpie'
            target.parent.mkdir(parents=True); target.write_text('my original command\n')
            for remove in (False, True):
                self.assertNotEqual(self.run_script(w.wrapper_script('codex', profile(), remove), root).returncode, 0)
                self.assertEqual(target.read_text(), 'my original command\n')

    def test_symlink_survives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root / '.local/bin/codex-magpie'
            target.parent.mkdir(parents=True); original = root / 'original'
            original.write_text(w.MARKER); target.symlink_to(original)
            self.assertNotEqual(self.run_script(w.wrapper_script('codex', profile()), root).returncode, 0)
            self.assertTrue(target.is_symlink()); self.assertEqual(original.read_text(), w.MARKER)

    def test_generated_wrapper_preserves_literal_model_and_arguments(self):
        import shlex
        p = profile(); p['model'] = 'literal/$(id); x'
        content = w.wrapper_content('codex', p)
        command = shlex.split(content.split('exec ', 1)[1])
        self.assertEqual(command[command.index('-m') + 1], p['model'])
        self.assertEqual(command[-1], '$@')
        self.assertIn('model_providers.magpie_ssh.requires_openai_auth=false', command)

    def test_claude_clears_overriding_auth_modes(self):
        content = w.wrapper_content('claude', profile())
        for value in ('CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_USE_BEDROCK', 'ANTHROPIC_API_KEY'):
            self.assertIn('-u ' + value, content)
        self.assertIn('ANTHROPIC_BASE_URL=http://127.0.0.1:13425', content)

    def test_ssh_uses_strict_auth_and_no_shell_interpolation(self):
        args = w.ssh_args(profile(), '/tmp/socket')
        self.assertIn('BatchMode=yes', args)
        self.assertIn('StrictHostKeyChecking=yes', args)

    def manager(self, directory):
        return w.Manager(Path(directory) / 'magpie', ROOT, '/fake/magpie')

    def test_reconnect_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory); p = profile(); p['enabled'] = True
            manager.state['profiles'] = [p]
            proc = MagicMock(); proc.poll.return_value = 255
            manager.processes[p['id']] = proc
            (manager.runtime / (p['id'] + '.log')).write_text('connection lost')
            with patch.object(w.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
                manager.monitor()
            self.assertEqual(manager.statuses[p['id']]['state'], 'retrying')
            manager.retry[p['id']] = 0
            with patch.object(manager, 'start') as start:
                manager.monitor(); start.assert_called_once_with(p)

    def test_save_disables_previous_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory); p = profile(); p['enabled'] = True
            manager.state['profiles'] = [p.copy()]
            with patch.object(manager, 'stop') as stop:
                manager.handle({'action': 'save', 'profile': p})
            stop.assert_called_once()
            self.assertFalse(manager.state['profiles'][0]['enabled'])

    def test_install_requires_live_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory); p = profile(); manager.state['profiles'] = [p]
            with patch.object(manager, 'ssh') as ssh:
                with self.assertRaisesRegex(ValueError, '先连接'):
                    manager.handle({'action': 'install', 'profileId': p['id'], 'agent': 'codex'})
                ssh.assert_not_called()

    def test_stale_response_cannot_replace_new_request(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            with patch.object(manager, 'entry', return_value={'options': {'request': {'id': 'new'}}}), \
                 patch.object(w.subprocess, 'run') as run:
                manager.publish('old', 'old result')
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
