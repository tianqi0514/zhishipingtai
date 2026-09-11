/** Generate isolated development credentials without printing any secret.
 * Existing files are never overwritten. Run from the enterprise directory.
 */
import { randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const root = resolve(import.meta.dirname, '../..');
const directory = resolve(root, '.local-dev');
mkdirSync(directory, { recursive: true, mode: 0o700 });
for (const name of ['app.env', 'remote.env', 'ssh/known_hosts', 'authorized_keys']) {
  if (existsSync(resolve(directory, name))) throw new Error(`Refusing to replace existing ${name}`);
}
// Validate all prerequisites before writing any configuration.
const host = process.env.DEV_SSH_HOST || '10.5.113.232';
const hosts = execFileSync('ssh-keygen', ['-F', host], { encoding: 'utf8' }).split('\n').filter(s => s.includes(' ssh-ed25519 '));
if (!hosts.length) throw new Error('Verify the server host key before provisioning');
const pubkey = readFileSync(resolve(directory, 'ssh/id_ed25519.pub'), 'utf8').trim();
if (!pubkey.startsWith('ssh-ed25519 ')) throw new Error('Expected dedicated Ed25519 key');
const password = () => randomBytes(24).toString('hex');
const postgres = password(), rabbit = password(), minio = password();
const remote = { POSTGRES_PASSWORD: postgres, RABBITMQ_PASSWORD: rabbit, MINIO_ROOT_PASSWORD: minio };
const base = JSON.parse(execFileSync('docker', ['compose', '-f', 'compose.yaml', 'config', '--format', 'json'], { cwd: root, encoding: 'utf8' }));
const app = {
  ...base.services.api.environment,
  DATABASE_URL: `postgresql+psycopg://semantica_dev:${postgres}@dev-tunnel:5432/semantica_dev`,
  REDIS_URL: 'redis://dev-tunnel:6379/0',
  CELERY_RESULT_BACKEND: 'redis://dev-tunnel:6379/1',
  CELERY_BROKER_URL: `amqp://semantica_dev:${rabbit}@dev-tunnel:5672/semantica_dev`,
  OBJECT_STORE_ENDPOINT: 'dev-tunnel:9000', OBJECT_STORE_ACCESS_KEY: 'semantica_dev',
  OBJECT_STORE_SECRET_KEY: minio, OBJECT_STORE_BUCKET: 'knowledge',
  OPENSEARCH_URL: 'http://dev-tunnel:9200', QDRANT_URL: 'http://dev-tunnel:6333',
  FALKORDB_HOST: 'dev-tunnel', FALKORDB_PORT: '6380',
};
for (const [name, values] of [['app.env', app], ['remote.env', remote]]) {
  if (Object.values(values).some(v => /[\r\n]/.test(String(v)))) throw new Error('Multiline environment values are not supported');
  writeFileSync(resolve(directory, name), Object.entries(values).map(([k, v]) => `${k}=${v}\n`).join(''), { flag: 'wx', mode: 0o600 });
}
// Copy only the previously trusted host key, never trust a fresh network scan.
writeFileSync(resolve(directory, 'ssh/known_hosts'), hosts.join('\n') + '\n', { flag: 'wx', mode: 0o600 });
const ports = [25432, 26379, 25672, 29000, 29200, 26333, 26380];
const restrictions = ['restrict', 'port-forwarding', ...ports.map(p => `permitopen="127.0.0.1:${p}"`)];
writeFileSync(resolve(directory, 'authorized_keys'), restrictions.join(',') + ' ' + pubkey + '\n', { flag: 'wx', mode: 0o600 });
console.log('Created private development configuration and restricted public-key grant (no secrets printed).');
