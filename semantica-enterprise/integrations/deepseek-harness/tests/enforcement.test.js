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
  ])
  assert.equal(typeof listeners.get('agent/turn-stopping'), 'function')
  assert.equal(typeof listeners.get('agent/request'), 'function')
})


test('structured execute schema is aligned with the strict platform Plan and IR contracts', () => {
  const { tools } = fixture()
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
    evidenceRequirements('销售额是多少，统计口径依据哪份制度？'),
    ['knowledge_search', 'structured_execute_query'],
  )
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
  assert.equal(installed.tools.length, 11)
  assert.equal(installed.sections.length, 1)
  assert.equal(installed.listeners.size, 2)
  installed.dispose()
  assert.equal(installed.tools.length, 0)
  assert.equal(installed.sections.length, 0)
  assert.equal(installed.listeners.size, 0)
})
