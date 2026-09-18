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
    'writing_stage_started',
    'writing_stage_finished',
  ]) {
    assert.match(source, new RegExp(`['\"]${eventType}['\"]`))
  }
  assert.match(source, /occurred_at: occurredAt/)
  assert.match(source, /duration_ms: turnState\.startedAt/)
  assert.match(source, /normalizePublicError/)
  assert.match(source, /请求参数不符合结构化查询协议/)
  assert.ok(
    source.lastIndexOf("if (['failed', 'error', 'blocked'].includes(reason))")
      < source.lastIndexOf('if (!evidenceSatisfied(turnState))'),
    'provider errors must be projected before evidence-policy failures',
  )
  assert.match(source, /failure\.message \|\| reason/)
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

test('runtime makes the platform thinking switch an explicit SDK effort', () => {
  const runtime = readFileSync(new URL('../runtime.js', import.meta.url), 'utf8')
  const patch = readFileSync(new URL('../cordis.patch.yml', import.meta.url), 'utf8')
  assert.match(runtime, /reasoningEffort: model\.enable_thinking === false \? 'off' : 'high'/)
  assert.match(patch, /reasoningEfforts:\s+off:\s+high: high/s)
  assert.match(patch, /supportsDeveloperRole: false/)
  assert.match(patch, /thinkingFormat:/)
})

test('runtime gives formal-writing compaction a bounded non-truncating budget', () => {
  const runtime = readFileSync(new URL('../runtime.js', import.meta.url), 'utf8')
  const patch = readFileSync(new URL('../cordis.patch.yml', import.meta.url), 'utf8')
  assert.match(runtime, /function compactionMaxTokens\(model\)/)
  assert.match(runtime, /model\?\.compaction_max_tokens \|\| 2048/)
  assert.match(runtime, /modelContextWindow\(model\), compactionMaxTokens\(model\)/)
  assert.match(runtime, /DSH_COMPACTION_MAX_TOKENS: String\(compactionMaxTokens\(model\)\)/)
  assert.match(patch, /maxTokens: !!js Number\(process\.env\.DSH_COMPACTION_MAX_TOKENS \|\| 2048\)/)
})

test('formal report final render keeps the section envelope strict', () => {
  const source = readFileSync(new URL('../index.js', import.meta.url), 'utf8')
  assert.match(source, /章节对象只允许 section_key、title、content_nodes、citation_refs、metric_refs、inference_refs、warnings/)
  assert.match(source, /table 只能有 type 和二维 items，不得有 text/)
  assert.match(source, /input_refs 只填写项目已确认事实的 fact_key/)
  assert.match(source, /writing_fact_refs、writing_evidence_refs、writing_relation_refs/)
  assert.match(source, /禁止 status、state、progress/)
})
