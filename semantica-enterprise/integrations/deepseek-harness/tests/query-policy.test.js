import assert from 'node:assert/strict'
import test from 'node:test'

import {
  currentRetrievalSettings,
  currentUserQuery,
  evidenceRequirements,
  requiresGraphQuery,
  requiresKnowledgeReason,
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
  assert.deepEqual(
    evidenceRequirements('NexusOne 的销售额是多少？'),
    ['structured_schema_search', 'structured_execute_query'],
  )
  assert.deepEqual(
    evidenceRequirements('今年销售额是多少，统计口径依据哪份制度？'),
    ['knowledge_search', 'structured_schema_search', 'structured_execute_query'],
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


test('requires graph and Semantica reasoning for explainable impact chains', () => {
  const question = '东方智造交付延期会影响哪些项目和责任部门？请说明完整关系路径。'
  assert.equal(requiresGraphQuery(question), true)
  assert.equal(requiresKnowledgeReason(question), true)
  assert.deepEqual(evidenceRequirements(question), [
    'knowledge_search', 'knowledge_graph_query', 'knowledge_reason',
  ])
  assert.equal(requiresGraphQuery('采购制度适用于哪些单位？'), false)
  assert.equal(requiresKnowledgeReason('采购制度适用于哪些单位？'), false)
  assert.deepEqual(evidenceRequirements(question, { use_graph: false }), [
    'knowledge_search',
  ])
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


test('separates platform retrieval settings from the user question', () => {
  const events = [{
    type: 'user/message',
    data: {
      content: [{
        type: 'text',
        text: '东方智造延期会影响哪些项目？\n\n<chuanshen-retrieval-settings>{"use_keyword":true,"use_vector":true,"use_graph":false,"use_reranker":false,"top_k":10}</chuanshen-retrieval-settings>',
      }],
      source: { kind: 'user' },
    },
  }]
  assert.equal(currentUserQuery(events), '东方智造延期会影响哪些项目？')
  assert.deepEqual(currentRetrievalSettings(events), {
    use_keyword: true,
    use_vector: true,
    use_graph: false,
    use_reranker: false,
    top_k: 10,
  })
  assert.deepEqual(
    evidenceRequirements(currentUserQuery(events), currentRetrievalSettings(events)),
    ['knowledge_search'],
  )
})


test('uses writing evidence tools for formal reports without inventing a database requirement', () => {
  const prompt = '[妙笔正式报告生成]\n九章正文不少于 8462 字，包含资源数量、金额和完成率。'
  assert.deepEqual(evidenceRequirements(prompt), [
    'writing_get_project_context',
    'knowledge_search',
    'writing_create_outline_draft',
    'writing_generate_section_draft',
  ])
  assert.equal(evidenceRequirements(prompt).includes('structured_execute_query'), false)
  assert.deepEqual(
    evidenceRequirements('[妙笔写作任务] 请将选中段落改写得更正式。'),
    ['writing_get_project_context'],
  )
})
