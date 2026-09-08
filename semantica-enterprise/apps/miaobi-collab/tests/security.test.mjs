import assert from 'node:assert/strict';
import test from 'node:test';

import jwt from 'jsonwebtoken';

const secret = 'test-collaboration-secret-that-is-at-least-32-bytes';
const tenant = '11111111-1111-1111-1111-111111111111';
const document = '22222222-2222-2222-2222-222222222222';
const room = `writing-${tenant}-${document}`;

test('room-scoped collaboration token rejects cross-document reuse', async () => {
  const { verifyCollaborationToken } = await import('../src/token.mjs');
  const token = jwt.sign({ sub: 'user', room, role: 'editor' }, secret, {
    audience: 'miaobi-collaboration',
    expiresIn: 60,
  });
  assert.equal(verifyCollaborationToken(token, room, secret).sub, 'user');
  assert.throws(() => verifyCollaborationToken(token, `writing-${tenant}-33333333-3333-3333-3333-333333333333`, secret));
});

test('expired and forged tokens are rejected', async () => {
  const { verifyCollaborationToken } = await import('../src/token.mjs');
  const expired = jwt.sign({ sub: 'user', room, role: 'editor' }, secret, {
    audience: 'miaobi-collaboration', expiresIn: -1,
  });
  assert.throws(() => verifyCollaborationToken(expired, room, secret));
  const forged = jwt.sign({ sub: 'user', room, role: 'editor' }, `${secret}-wrong`, {
    audience: 'miaobi-collaboration', expiresIn: 60,
  });
  assert.throws(() => verifyCollaborationToken(forged, room, secret));
});
