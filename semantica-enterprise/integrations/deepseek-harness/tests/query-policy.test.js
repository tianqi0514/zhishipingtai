import assert from 'node:assert/strict'
import test from 'node:test'

import {
  currentUserQuery,
  evidenceRequirements,
  requiresKnowledgeSearch,
  requiresStructuredQuery,
} from '../query-policy.js'


test('allows conversational questions without weakening knowledge retrieval', () => {
  assert.equal(requiresKnowledgeSearch('你是谁？'), false)
  assert.equal(requiresKnowledgeSearch('你好'), false)
  assert.equal(requiresKnowledgeSearch('你能做什么'), false)
  assert.equal(requiresKnowledgeSearch('NexusOne 支持哪些数据源？'), true)
  assert.equal(requiresKnowledgeSearch('它的主要优势是什么？'), true)
})


test('requires deterministic structured evidence for numeric questions', () => {
  assert.equal(requiresStructuredQuery('2026 年 NexusOne 的销售总额是多少？'), true)
  assert.equal(requiresStructuredQuery('NexusOne 产品手册写了什么？'), false)
  assert.deepEqual(evidenceRequirements('NexusOne 的销售额是多少？'), ['structured_execute_query'])
  assert.deepEqual(
    evidenceRequirements('今年销售额是多少，统计口径依据哪份制度？'),
    ['knowledge_search', 'structured_execute_query'],
  )
  assert.equal(requiresStructuredQuery('销售额统计口径依据哪份制度？'), false)
  assert.equal(requiresStructuredQuery('销售额的定义是什么？'), false)
  assert.equal(requiresStructuredQuery('销售额是多少？'), true)
  assert.deepEqual(
    evidenceRequirements('销售额统计口径依据哪份制度？'),
    ['knowledge_search'],
  )
  assert.deepEqual(evidenceRequirements('你好'), [])
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
