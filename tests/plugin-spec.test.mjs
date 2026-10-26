import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { resolvePluginSpec } from '../plugin-spec.mjs';

function fixture(spec, dependency, fn) {
  const dir = mkdtempSync(join(tmpdir(), 'magpie-spec-'));
  const config = join(dir, 'config'), root = join(dir, 'plugin');
  mkdirSync(join(config, 'plugins'), { recursive: true });
  mkdirSync(root);
  const value = spec === 'local' ? root : spec;
  writeFileSync(join(root, 'package.json'), JSON.stringify({ name: 'magpie-ssh-remote' }));
  writeFileSync(join(config, 'plugins.json'), JSON.stringify({ plugins: [{ spec: value }] }));
  writeFileSync(join(config, 'plugins/package.json'), JSON.stringify({ dependencies: dependency ? { 'magpie-ssh-remote': dependency } : {} }));
  try { fn({ config, root, value }); }
  finally { rmSync(dir, { recursive: true, force: true }); }
}

test('local directory install keeps its exact spec', () => {
  fixture('local', null, ({ config, root, value }) => assert.equal(resolvePluginSpec(config, root), value));
});
test('GitHub install resolves through Bun dependency metadata', () => {
  fixture('github:example/magpie-ssh-remote', 'github:example/magpie-ssh-remote',
    ({ config, root, value }) => assert.equal(resolvePluginSpec(config, root), value));
});
test('HTTPS Git and GitHub shorthand identify the same source', () => {
  fixture('git+https://github.com/example/magpie-ssh-remote.git#main', 'github:example/magpie-ssh-remote#main',
    ({ config, root, value }) => assert.equal(resolvePluginSpec(config, root), value));
});
test('versioned package install keeps its versioned spec', () => {
  fixture('magpie-ssh-remote@0.1.0', null,
    ({ config, root, value }) => assert.equal(resolvePluginSpec(config, root), value));
});
test('unknown install does not launch a helper against another plugin', () => {
  fixture('other-package', null,
    ({ config, root }) => assert.throws(() => resolvePluginSpec(config, root), /Cannot identify/));
});
