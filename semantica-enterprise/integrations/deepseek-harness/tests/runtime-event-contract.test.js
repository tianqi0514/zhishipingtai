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
  ]) {
    assert.match(source, new RegExp(`['\"]${eventType}['\"]`))
  }
  assert.match(source, /occurred_at: occurredAt/)
  assert.match(source, /duration_ms: turnState\.startedAt/)
  assert.doesNotMatch(source, /chain[_-]?of[_-]?thought/i)
})
