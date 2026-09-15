/** Link only pinned Harness workspace packages into the runtime resolver. */
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, symlinkSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';

const workspace = resolve(import.meta.dirname, '../../..');
const scope = join(workspace, 'node_modules', '@deepseek-ai');
mkdirSync(scope, { recursive: true });
const seen = new Set();

function visit(directory) {
  if (!existsSync(directory)) return;
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (!entry.isDirectory() || ['node_modules', '.git', 'dist', 'lib', 'src'].includes(entry.name)) continue;
    const child = join(directory, entry.name);
    const manifestPath = join(child, 'package.json');
    if (!existsSync(manifestPath)) {
      visit(child);
      continue;
    }
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
    if (!String(manifest.name || '').startsWith('@deepseek-ai/')) continue;
    const name = manifest.name.split('/')[1];
    if (seen.has(name)) throw new Error(`duplicate pinned Harness package: ${name}`);
    seen.add(name);
    const destination = join(scope, name);
    const target = relative(dirname(destination), child);
    if (existsSync(destination) || (lstatSyncSafe(destination)?.isSymbolicLink())) continue;
    symlinkSync(target, destination, 'dir');
  }
}

function lstatSyncSafe(path) {
  try { return lstatSync(path); } catch { return null; }
}

for (const root of ['vendor', 'packages', 'apps', 'native']) visit(join(workspace, root));
if (!seen.has('cordis') || !seen.has('dsh-scope')) {
  throw new Error('pinned Harness workspace peer inventory is incomplete');
}
process.stdout.write(`linked ${seen.size} pinned Harness workspace packages\n`);
