import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_worker import w, profile


class NativeTests(unittest.TestCase):
    def execute(self, root, action, agent=None, model=None):
        fake = root / 'magpie'
        fake.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
root=Path(os.environ['MAGPIE_TEST_ROOT'])
args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps(args)+'\\n')
if args==['healthcheck']:sys.exit(0 if (root/'healthy').exists() else 1)
if args[:2]==['daemon','start']:(root/'healthy').touch()
if len(args)==2 and args[0]=='provider':sys.exit(0 if (root/'provider').exists() else 1)
if args[:2]==['provider','add']:(root/'provider').touch()
if args[-1:] == ['--help']:
 print('usage only' if args[0]=='not-an-agent' else 'fields: model, effort')
else:print('native operation complete')
''')
        fake.chmod(0o700)
        p = profile()
        if model:
            p['model'] = model
        script = w.native_script(action, p, agent).replace('$HOME', '$MAGPIE_TEST_REMOTE_HOME')
        env = dict(os.environ, MAGPIE_TEST_ROOT=str(root), MAGPIE_TEST_REMOTE_HOME=str(root),
                   PATH=str(root) + os.pathsep + os.environ['PATH'])
        return subprocess.run(['sh', '-s'], input=script, env=env, text=True, capture_output=True)

    def calls(self, root):
        return [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]

    def test_setup_starts_native_service_and_adds_remote_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.execute(root, 'native-setup')
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = self.calls(root)
            self.assertIn(['daemon', 'install'], calls)
            self.assertIn(['daemon', 'start'], calls)
            add = next(c for c in calls if c[:2] == ['provider', 'add'])
            self.assertEqual(add[2:4], ['remote-magpie', 'magpie-ssh'])
            self.assertIn('url=http://127.0.0.1:13425', add)
            self.assertIn('id=ssh-desktop-ab0123456789abcd', add)

    def test_second_setup_updates_without_duplicate_provider_or_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.execute(root, 'native-setup')
            result = self.execute(root, 'native-setup')
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = self.calls(root)
            self.assertEqual(sum(c[:2] == ['provider', 'add'] for c in calls), 1)
            self.assertEqual(sum(c[:2] == ['daemon', 'install'] for c in calls), 1)
            self.assertTrue(any(c[:2] == ['provider', 'set'] for c in calls))

    def test_all_agent_ids_share_the_same_native_configuration_path(self):
        for agent in ('omp', 'pi', 'dsh', 'codex', 'claude', 'future-agent'):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                model = 'provider/literal $(id); space'
                result = self.execute(root, 'native-apply', agent, model)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls(root)[-1], [agent, 'ssh-desktop-ab0123456789abcd/' + model])

    def test_restore_uses_native_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(self.execute(root, 'native-restore', 'dsh').returncode, 0)
            self.assertEqual(self.calls(root)[-1], ['dsh', 'default'])

    def test_nonagent_command_cannot_be_used_to_change_configuration(self):
        for agent in ('provider', '--help', 'bad;id', 'daemon', 'a b'):
            with self.subTest(agent=agent), self.assertRaises(ValueError):
                w.native_script('native-apply', profile(), agent)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertNotEqual(self.execute(root, 'native-apply', 'not-an-agent').returncode, 0)
            self.assertEqual(self.calls(root), [['not-an-agent', '--help']])

    def test_inspect_uses_server_agent_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.execute(root, 'native-inspect')
            self.assertEqual(self.calls(root), [['--version'], ['agents']])

    def test_manager_requires_tunnel_for_native_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = w.Manager(Path(directory) / 'magpie', directory, '/fake/magpie')
            p = profile(); manager.state['profiles'] = [p]
            with patch.object(manager, 'ssh') as ssh:
                with self.assertRaisesRegex(ValueError, '先连接'):
                    manager.handle({'action': 'native-setup', 'profileId': p['id']})
                ssh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
