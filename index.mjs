import { spawn } from 'node:child_process';
import { mkdirSync, openSync, closeSync, existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolvePluginSpec } from './plugin-spec.mjs';

export default async function SSHRemotePlugin() {
  const root = dirname(fileURLToPath(import.meta.url));
  const config = join(process.env.XDG_CONFIG_HOME || join(homedir(), '.config'), 'magpie');
  const spec = resolvePluginSpec(config, root);
  const runtime = join(config, 'ssh-remote');
  mkdirSync(runtime, { recursive: true, mode: 0o700 });
  const log = openSync(join(runtime, 'worker.log'), 'a', 0o600);
  const magpie = process.env.MAGPIE_SSH_BIN ||
    (existsSync('/Applications/magpie.app/Contents/MacOS/magpie') ?
      '/Applications/magpie.app/Contents/MacOS/magpie' : 'magpie');
  const python = process.env.MAGPIE_SSH_PYTHON ||
    (existsSync('/usr/bin/python3') ? '/usr/bin/python3' : 'python3');
  try {
    const child = spawn(python, [join(root, 'worker.py'), '--config', config,
      '--plugin', spec, '--magpie', magpie], {
      detached: true, stdio: ['ignore', log, log],
    });
    child.on('error', error => console.error('SSH Remote helper:', error.message));
    child.unref();
  } finally { closeSync(log); }
  return {};
}
