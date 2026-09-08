import assert from 'node:assert/strict'
import test from 'node:test'

import { apply } from '../index.js'
import { evidenceRequirements } from '../query-policy.js'


function fixture() {
  const listeners = new Map()
  const tools = []
  const sections = []
  const disposers = []
  const ctx = {
    systemPrompt: { section(section) { sections.push(section); return () => sections.splice(sections.indexOf(section), 1) } },
    tools: { register(tool) { tools.push(tool); return () => tools.splice(tools.indexOf(tool), 1) } },
    effect(setup) { const dispose = setup(); if (dispose) disposers.push(dispose); return dispose },
    on(name, listener) {
      listeners.set(name, listener)
      const dispose = () => listeners.delete(name)
      disposers.push(dispose)
      return dispose
    },
  }
  apply(ctx)
  return { listeners, sections, tools, dispose: () => [...disposers].reverse().forEach(item => item()) }
}


test('registers typed knowledge tools and turn enforcement', () => {
  const { listeners, tools } = fixture()
  assert.deepEqual(tools.map(tool => tool.name), [
    'knowledge_search',
    'knowledge_get_fragment',
    'knowledge_graph_query',
    'knowledge_reason',
    'knowledge_get_document_profile',
    'structured_schema_search',
    'structured_get_object',
    'structured_find_relation_path',
    'structured_inspect_values',
    'structured_execute_query',
    'knowledge_list_spaces',
    'writing_get_project_context',
    'writing_get_document_outline',
    'writing_create_outline_draft',
    'writing_generate_section_draft',
    'writing_bind_evidence',
    'writing_validate_document',
    'writing_get_stale_blocks',
    'writing_recompute_impacts',
    'writing_compare_alternative_plans',
    'writing_prepare_export',
  ])
  assert.equal(typeof listeners.get('agent/turn-stopping'), 'function')
  assert.equal(typeof listeners.get('agent/request'), 'function')
})


test('structured execute schema is aligned with the strict platform Plan and IR contracts', () => {
  const { tools, sections } = fixture()
  const tool = tools.find(item => item.name === 'structured_execute_query')
  const plan = tool.parameters.properties.semantic_query_plan
  const ir = tool.parameters.properties.query_ir
  assert.deepEqual(plan.properties.version.enum, ['chuanshen.semantic-query-plan/v1'])
  assert.deepEqual(ir.properties.version.enum, ['chuanshen.query-ir/v1'])
  assert.ok(ir.properties.select.items.properties.expression.required.includes('kind'))
  assert.equal(ir.properties.select.items.properties.expression.properties.attribute_id.type, 'string')
  assert.equal(ir.properties.select.items.properties.expression.properties.binding.type, 'string')
  assert.equal(ir.properties.select.items.properties.expression.properties.arguments.type, 'array')
  assert.equal(ir.properties.where.oneOf, undefined)
  assert.equal(ir.properties.having.oneOf, undefined)
  assert.match(sections[0].text, /成功后必须直接使用该结果/)
  assert.match(sections[0].text, /引用编号是不可重排的片段外键/)
  assert.match(sections[0].text, /重新调用 structured_schema_search/)
  assert.match(sections[0].text, /不得声称已经查询知识图谱/)
  assert.match(sections[0].text, /不得展示 UUID/)
  assert.match(sections[0].text, /不得虚构人工审核/)
  assert.match(tools.find(item => item.name === 'knowledge_search').description, /citation_label/)
  assert.match(tools.find(item => item.name === 'structured_get_object').description, /required_filters/)
  assert.match(tools.find(item => item.name === 'structured_get_object').description, /required_relationships/)
  assert.match(tool.description, /固定筛选同时写入 Plan filters 和 IR where/)
  assert.match(tool.description, /关联 EXISTS/)
  assert.match(sections[1].text, /writing_get_project_context/)
  assert.match(sections[1].text, /最终回答禁止出现/)
  assert.match(sections[1].text, /Session Event/)
  assert.match(sections[1].text, /修订建议/)
  assert.match(sections[1].text, /不超过 N 字/)
  assert.match(sections[1].text, /文档引用只能紧跟/)
  assert.match(sections[1].text, /writing_compare_alternative_plans/)
  assert.match(tools.find(item => item.name === 'writing_get_project_context').description, /方案摘要/)
  assert.match(tools.find(item => item.name === 'writing_compare_alternative_plans').description, /仅当用户明确请求/)
  assert.ok(tools.find(item => item.name === 'writing_bind_evidence').parameters.required.includes('query_run_id'))
})


test('applies the platform model temperature through the plugin boundary', async () => {
  const previous = process.env.DSH_MODEL_TEMPERATURE
  process.env.DSH_MODEL_TEMPERATURE = '1'
  try {
    const { listeners } = fixture()
    const request = await listeners.get('agent/request')({}, async () => ({ provider: 'p', model: 'm' }))
    assert.equal(request.temperature, 1)
  } finally {
    if (previous === undefined) delete process.env.DSH_MODEL_TEMPERATURE
    else process.env.DSH_MODEL_TEMPERATURE = previous
  }
})


test('steers an evidence search when a turn tries to finish without one', () => {
  const { listeners } = fixture()
  const steered = []
  const agent = {
    session: { events: [{
      type: 'user/message',
      data: { content: [{ type: 'text', text: 'NexusOne 的定位是什么？' }], source: { kind: 'user' } },
    }] },
    steer(message) { steered.push(message) },
  }
  listeners.get('agent/turn-stopping')({
    agent,
    turn: 1,
    signal: new AbortController().signal,
  })
  assert.equal(steered.length, 1)
  assert.match(steered[0].content[0].text, /knowledge_search/)
})


test('numeric questions require structured execution, not prose search', () => {
  const { listeners } = fixture()
  const steered = []
  const agent = {
    session: { events: [
      { type: 'user/message', data: { content: [{ type: 'text', text: '销售总额是多少？' }], source: { kind: 'user' } } },
      { type: 'tool/call', data: { turn: 4, name: 'knowledge_search' } },
    ] },
    steer(message) { steered.push(message) },
  }
  listeners.get('agent/turn-stopping')({ agent, turn: 4, signal: new AbortController().signal })
  assert.equal(steered.length, 1)
  assert.match(steered[0].content[0].text, /structured_execute_query/)
  assert.match(steered[0].content[0].text, /structured_schema_search/)
})


test('mixed metric definition questions require both evidence channels', () => {
  const { listeners } = fixture()
  const steered = []
  const agent = {
    session: { events: [
      { type: 'user/message', data: { content: [{ type: 'text', text: '销售额是多少，口径依据什么制度？' }], source: { kind: 'user' } } },
      { type: 'tool/call', data: { turn: 5, name: 'structured_execute_query' } },
    ] },
    steer(message) { steered.push(message) },
  }
  listeners.get('agent/turn-stopping')({ agent, turn: 5, signal: new AbortController().signal })
  assert.equal(steered.length, 1)
  assert.match(steered[0].content[0].text, /knowledge_search/)
})


test('documentary follow-ups do not repeat an already completed metric query', () => {
  assert.deepEqual(
    evidenceRequirements('这个统计口径依据哪份制度？'),
    ['knowledge_search'],
  )
  assert.deepEqual(
    evidenceRequirements('销售额统计口径依据哪份制度？'),
    ['knowledge_search'],
  )
  assert.deepEqual(
    evidenceRequirements('销售额是多少，统计口径依据哪份制度？'),
    ['knowledge_search', 'structured_schema_search', 'structured_execute_query'],
  )
})


test('impact-chain questions require graph query and governed reasoning', () => {
  const { listeners } = fixture()
  const steered = []
  const agent = {
    session: { events: [
      { type: 'user/message', data: { content: [{ type: 'text', text: '东方智造延期会影响哪些项目？请给出完整关系路径。' }], source: { kind: 'user' } } },
      { type: 'tool/call', data: { turn: 6, callId: 'search-6', name: 'knowledge_search' } },
      { type: 'tool/result', data: { callId: 'search-6', content: [] } },
    ] },
    steer(message) { steered.push(message) },
  }
  listeners.get('agent/turn-stopping')({ agent, turn: 6, signal: new AbortController().signal })
  assert.equal(steered.length, 1)
  assert.match(steered[0].content[0].text, /knowledge_graph_query/)
  assert.match(steered[0].content[0].text, /knowledge_reason/)
})


test('does not enforce graph tools when the platform disables graph retrieval', () => {
  const { listeners } = fixture()
  const steered = []
  const agent = {
    session: { events: [
      {
        type: 'user/message',
        data: {
          content: [{
            type: 'text',
            text: '东方智造延期会影响哪些项目？请给出完整关系路径。\n\n<chuanshen-retrieval-settings>{"use_keyword":true,"use_vector":true,"use_graph":false,"use_reranker":false,"top_k":10}</chuanshen-retrieval-settings>',
          }],
          source: { kind: 'user' },
        },
      },
      { type: 'tool/call', data: { turn: 7, callId: 'search-7', name: 'knowledge_search' } },
      { type: 'tool/result', data: { callId: 'search-7', content: [] } },
    ] },
    steer(message) { steered.push(message) },
  }
  listeners.get('agent/turn-stopping')({ agent, turn: 7, signal: new AbortController().signal })
  assert.equal(steered.length, 0)
})


test('does not steer after knowledge_search was durably logged', () => {
  const { listeners } = fixture()
  const steered = []
  const agent = {
    session: {
      events: [
        { type: 'tool/call', data: { turn: 2, callId: 'call-2', name: 'knowledge_search' } },
        { type: 'tool/result', data: { callId: 'call-2', content: [] } },
      ],
    },
    steer(message) { steered.push(message) },
  }
  listeners.get('agent/turn-stopping')({
    agent,
    turn: 2,
    signal: new AbortController().signal,
  })
  assert.equal(steered.length, 0)
})


test('does not force retrieval for a direct identity question', () => {
  const { listeners } = fixture()
  const steered = []
  const agent = {
    session: {
      events: [{
        type: 'user/message',
        data: {
          content: [{ type: 'text', text: '你是谁？' }],
          source: { kind: 'user' },
        },
      }],
    },
    steer(message) { steered.push(message) },
  }
  listeners.get('agent/turn-stopping')({
    agent,
    turn: 3,
    signal: new AbortController().signal,
  })
  assert.equal(steered.length, 0)
})


test('unloads every tool, prompt section and event listener', () => {
  const installed = fixture()
  assert.equal(installed.tools.length, 21)
  assert.equal(installed.sections.length, 2)
  assert.equal(installed.listeners.size, 2)
  installed.dispose()
  assert.equal(installed.tools.length, 0)
  assert.equal(installed.sections.length, 0)
  assert.equal(installed.listeners.size, 0)
})
