import assert from 'node:assert/strict'
import test from 'node:test'

import { apply } from '../index.js'


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
    'knowledge_list_spaces',
  ])
  assert.equal(typeof listeners.get('agent/turn-stopping'), 'function')
  assert.equal(typeof listeners.get('agent/request'), 'function')
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
    session: { events: [] },
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


test('does not steer after knowledge_search was durably logged', () => {
  const { listeners } = fixture()
  const steered = []
  const agent = {
    session: {
      events: [{ type: 'tool/call', data: { turn: 2, name: 'knowledge_search' } }],
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
  assert.equal(installed.tools.length, 6)
  assert.equal(installed.sections.length, 1)
  assert.equal(installed.listeners.size, 2)
  installed.dispose()
  assert.equal(installed.tools.length, 0)
  assert.equal(installed.sections.length, 0)
  assert.equal(installed.listeners.size, 0)
})
