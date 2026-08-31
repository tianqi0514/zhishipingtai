import assert from 'node:assert/strict'
import test from 'node:test'

import { currentUserQuery, requiresKnowledgeSearch } from '../query-policy.js'


test('allows conversational questions without weakening knowledge retrieval', () => {
  assert.equal(requiresKnowledgeSearch('你是谁？'), false)
  assert.equal(requiresKnowledgeSearch('你好'), false)
  assert.equal(requiresKnowledgeSearch('你能做什么'), false)
  assert.equal(requiresKnowledgeSearch('NexusOne 支持哪些数据源？'), true)
  assert.equal(requiresKnowledgeSearch('它的主要优势是什么？'), true)
})


test('reads the latest real user message and ignores plugin steering', () => {
  const events = [
    {
      type: 'user/message',
      data: { content: [{ type: 'text', text: '你是谁' }], source: { kind: 'user' } },
    },
    {
      type: 'user/message',
      data: { content: [{ type: 'text', text: '协议校验' }], source: { kind: 'plugin' } },
    },
  ]
  assert.equal(currentUserQuery(events), '你是谁')
})
