import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


test('runtime projects observable event time and turn duration without exposing hidden reasoning', () => {
  const source = readFileSync(new URL('../runtime.js', import.meta.url), 'utf8')
  for (const eventType of [
    'turn_started',
    'step_started',
    'retrieval_started',
    'tool_started',
    'tool_finished',
    'retrieval_ranked',
    'answer_delta',
    'turn_completed',
    'turn_failed',
    'turn_cancelled',
    'structured_schema_search_started',
    'structured_schema_search_finished',
    'structured_object_loaded',
    'structured_plan_started',
    'structured_plan_validated',
    'structured_ir_validated',
    'structured_query_compiled',
    'structured_query_started',
    'structured_query_finished',
    'structured_query_failed',
    'structured_query_cancelled',
  ]) {
    assert.match(source, new RegExp(`['\"]${eventType}['\"]`))
  }
  assert.match(source, /occurred_at: occurredAt/)
  assert.match(source, /duration_ms: turnState\.startedAt/)
  assert.match(source, /normalizePublicError/)
  assert.match(source, /请求参数不符合结构化查询协议/)
  assert.doesNotMatch(source, /chain[_-]?of[_-]?thought/i)
})

test('runtime serializes cancel cleanup and evicts failed SDK initialization', () => {
  const source = readFileSync(new URL('../runtime.js', import.meta.url), 'utf8')
  assert.match(source, /const closingSessions = new Map\(\)/)
  assert.match(source, /if \(closing\) await closing/)
  assert.match(source, /DSH_INITIALIZE_TIMEOUT_MS \|\| 90000/)
  assert.match(source, /if \(!turnState\.startedAt\) await disposeSession\(sessionId, entry\)/)
  assert.match(source, /await disposeSession\(sessionId, entry\)/)
})
