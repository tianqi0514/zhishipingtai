import jwt from 'jsonwebtoken';

export function verifyCollaborationToken(token, documentName, sharedSecret) {
  const claims = jwt.verify(token, sharedSecret, {
    algorithms: ['HS256'],
    audience: 'miaobi-collaboration',
  });
  if (!claims || typeof claims !== 'object' || claims.room !== documentName) {
    throw new Error('协同房间与访问凭据不匹配');
  }
  if (!/^writing-[0-9a-f-]{36}-[0-9a-f-]{36}$/i.test(documentName)) {
    throw new Error('协同房间标识无效');
  }
  if (!['viewer', 'commenter', 'editor', 'reviewer', 'publisher', 'owner'].includes(String(claims.role))) {
    throw new Error('协同角色无效');
  }
  return claims;
}
