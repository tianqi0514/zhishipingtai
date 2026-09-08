import { createServer as createHttpServer } from 'node:http';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { Server } from '@hocuspocus/server';
import * as Y from 'yjs';

import { verifyCollaborationToken } from './token.mjs';

const port = Number.parseInt(process.env.PORT || '8092', 10);
const healthPort = Number.parseInt(process.env.HEALTH_PORT || '8093', 10);
const storageRoot = process.env.COLLABORATION_STORAGE_PATH || '/var/lib/miaobi-collab';
const secretFile = process.env.COLLABORATION_SECRET_FILE || '/run/secrets/agent_service_secret';
const secret = readFileSync(secretFile, 'utf8').trim();

if (secret.length < 32) throw new Error('协同编辑服务密钥长度不足');
await mkdir(storageRoot, { recursive: true, mode: 0o700 });

function statePath(documentName) {
  return join(storageRoot, `${documentName}.bin`);
}

async function loadDocument(documentName, document) {
  try {
    Y.applyUpdate(document, await readFile(statePath(documentName)));
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  return document;
}

async function storeDocument(documentName, document) {
  const path = statePath(documentName);
  const temporaryPath = `${path}.${process.pid}.tmp`;
  await writeFile(temporaryPath, Y.encodeStateAsUpdate(document), { mode: 0o600 });
  await rename(temporaryPath, path);
}

const hocuspocus = new Server({
  port,
  address: '0.0.0.0',
  debounce: 800,
  maxDebounce: 5000,
  quiet: true,
  async onAuthenticate(data) {
    const claims = verifyCollaborationToken(data.token, data.documentName, secret);
    if (['viewer', 'commenter'].includes(String(claims.role))) data.connection.readOnly = true;
    return {
      userId: claims.sub,
      tenantId: claims.tenant_id,
      projectId: claims.project_id,
      documentId: claims.document_id,
      role: claims.role,
    };
  },
  async onLoadDocument({ documentName, document }) {
    return loadDocument(documentName, document);
  },
  async onStoreDocument({ documentName, document }) {
    await storeDocument(documentName, document);
  },
}, { maxPayload: 4 * 1024 * 1024 });

const health = createHttpServer((request, response) => {
  if (request.url === '/health/live' || request.url === '/health/ready') {
    response.writeHead(200, { 'content-type': 'application/json; charset=utf-8' });
    response.end(JSON.stringify({ status: 'ready', websocket_port: port }));
    return;
  }
  response.writeHead(404).end();
});

await hocuspocus.listen();
health.listen(healthPort, '0.0.0.0');

let stopping = false;
async function shutdown() {
  if (stopping) return;
  stopping = true;
  health.close();
  await hocuspocus.destroy();
  process.exit(0);
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
