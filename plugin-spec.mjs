import { readFileSync, realpathSync } from 'node:fs';
import { join, resolve } from 'node:path';

const readJSON = path => {
  try { return JSON.parse(readFileSync(path, 'utf8')); }
  catch { return {}; }
};
const canonicalPath = path => {
  try { return realpathSync(path); }
  catch { return resolve(path); }
};
const gitSource = spec => spec.replace(/^git\+/, '')
  .replace(/^https:\/\/github\.com\//, 'github:')
  .replace(/\.git(?=#|$)/, '');

// The installed target is a folder, but Magpie's options are keyed by the
// original install spec (local path, npm package, or GitHub repository).
export function resolvePluginSpec(config, root) {
  const entries = readJSON(join(config, 'plugins.json')).plugins || [];
  const name = readJSON(join(root, 'package.json')).name;
  const local = entries.find(entry => {
    if (!/^(\/|\.|file:\/\/)/.test(entry.spec)) return false;
    return canonicalPath(entry.spec.replace(/^file:\/\//, '')) === canonicalPath(root);
  });
  if (local) return local.spec;
  const dependencies = readJSON(join(config, 'plugins', 'package.json')).dependencies || {};
  const source = dependencies[name];
  const installed = entries.filter(entry => !entry.off && (
    entry.spec === name || entry.spec.startsWith(name + '@') ||
    (source && gitSource(entry.spec) === gitSource(source))
  ));
  if (installed.length === 1) return installed[0].spec;
  throw new Error('Cannot identify this plugin in Magpie. Remove duplicate installs and add it again.');
}
