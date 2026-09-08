import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { HocuspocusProvider } from '@hocuspocus/provider';
import jwt from 'jsonwebtoken';
import * as Y from 'yjs';

const url = process.env.COLLABORATION_WS_URL || 'ws://127.0.0.1:8092';
const secretFile = process.env.COLLABORATION_SECRET_FILE || new URL('../../../deploy/secrets/agent_service_secret', import.meta.url);
const mode = process.argv[2] || 'write';
const marker = process.env.COLLABORATION_TEST_MARKER || 'p4-persistence-marker';
const clientCount = Math.max(2, Number.parseInt(process.env.COLLABORATION_CLIENTS || '2', 10));
const tenant = '11111111-1111-1111-1111-111111111111';
const document = '22222222-2222-2222-2222-222222222222';
const room = `writing-${tenant}-${document}`;
const secret = (await readFile(secretFile, 'utf8')).trim();
const token = jwt.sign({
  sub: 'collaboration-integration-user',
  tenant_id: tenant,
  project_id: '33333333-3333-3333-3333-333333333333',
  document_id: document,
  room,
  role: 'editor',
  jti: 'integration-probe',
}, secret, { algorithm: 'HS256', audience: 'miaobi-collaboration', expiresIn: 60 });

function open(documentState) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('协同同步超时')), 8000);
    const provider = new HocuspocusProvider({
      url,
      name: room,
      token,
      document: documentState,
      onSynced() { clearTimeout(timeout); resolve(provider); },
      onAuthenticationFailed(data) { clearTimeout(timeout); reject(new Error(data.reason || '协同鉴权失败')); },
    });
  });
}

const documents = Array.from({ length: mode === 'read' ? 1 : clientCount }, () => new Y.Doc());
const providers = await Promise.all(documents.map(open));
const firstDocument = documents[0];
if (mode === 'read') {
  assert.equal(firstDocument.getText('integration-probe').toString(), marker);
  providers[0].destroy();
  firstDocument.destroy();
  console.log('协同持久化恢复验证通过');
  process.exit(0);
}

const text = firstDocument.getText('integration-probe');
if (text.length) text.delete(0, text.length);
text.insert(0, marker);
const deadline = Date.now() + 5000;
while (
  documents.slice(1).some((documentState) => documentState.getText('integration-probe').toString() !== marker)
  && Date.now() < deadline
) {
  await new Promise((resolve) => setTimeout(resolve, 100));
}
for (const documentState of documents) {
  assert.equal(documentState.getText('integration-probe').toString(), marker);
}
for (const provider of providers) provider.destroy();
for (const documentState of documents) documentState.destroy();
await new Promise((resolve) => setTimeout(resolve, 1200));
console.log(`${clientCount} 客户端实时同步验证通过`);
