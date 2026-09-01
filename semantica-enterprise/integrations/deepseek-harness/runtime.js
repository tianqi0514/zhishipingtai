import { createServer } from 'node:http'
import { readFileSync } from 'node:fs'
import { DeepSeekHarness } from '/opt/deepseek-harness/packages/sdk/client/src/index.ts'
import { evidenceRequirements } from './query-policy.js'

const PORT = Number(process.env.PORT || 8090)
const PLATFORM_API = (process.env.PLATFORM_API || 'http://api:8080/api/v1').replace(/\/$/, '')
const SERVICE_SECRET_FILE = process.env.AGENT_SERVICE_SECRET_FILE || '/run/secrets/agent_service_secret'
const DSH_HOME = process.env.DSH_HOME || '/var/lib/chuanshen-harness'
const PATCH = process.env.DSH_PATCH || '/opt/deepseek-harness/packages/integration/chuanshen-knowledge/cordis.patch.yml'
const sessions = new Map()
const closingSessions = new Map()

function serviceSecret() {
  return readFileSync(SERVICE_SECRET_FILE, 'utf8').trim()
}

function json(res, status, value) {
  const body = JSON.stringify(value)
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body) })
  res.end(body)
}

async function body(req) {
  const parts = []
  let size = 0
  for await (const part of req) {
    size += part.length
    if (size > 256 * 1024) throw new Error('REQUEST_TOO_LARGE')
    parts.push(part)
  }
  return JSON.parse(Buffer.concat(parts).toString('utf8') || '{}')
}

async function modelConfig(sessionId) {
  const response = await fetch(`${PLATFORM_API}/internal/agent/model/${encodeURIComponent(sessionId)}`, {
    headers: { 'X-Agent-Service-Secret': serviceSecret() },
    signal: AbortSignal.timeout(15000),
  })
  const value = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(String(value.detail || `MODEL_CONFIG_${response.status}`))
  return value
}

function safeModelFingerprint(model) {
  return JSON.stringify([model.provider, model.model_name, model.base_url, model.max_tokens, model.max_retries, model.temperature])
}

async function harnessFor(sessionId) {
  const closing = closingSessions.get(sessionId)
  if (closing) await closing
  const model = await modelConfig(sessionId)
  const fingerprint = safeModelFingerprint(model)
  const current = sessions.get(sessionId)
  if (current && current.fingerprint === fingerprint) return current
  if (current) await current.harness.close().catch(() => {})
  const env = {
    ...process.env,
    DSH_MODEL_API_KEY: model.api_key,
    DSH_MODEL_BASE_URL: model.base_url || 'https://api.openai.com/v1',
    DSH_MODEL_NAME: model.model_name,
    DSH_MODEL_MAX_TOKENS: String(model.max_tokens || 4096),
    DSH_MODEL_MAX_RETRIES: String(model.max_retries || 2),
    DSH_MODEL_TEMPERATURE: String(model.temperature ?? 0.2),
    DSH_TELEMETRY_DISABLED: '1',
  }
  const harness = new DeepSeekHarness({
    profile: 'sdk-minimal',
    patches: [PATCH],
    dshHome: DSH_HOME,
    cwd: '/workspace',
    provider: 'knowledge-model',
    model: model.model_name,
    maxTokens: model.max_tokens || 4096,
    env,
    initializeTimeoutMs: Number(process.env.DSH_INITIALIZE_TIMEOUT_MS || 90000),
    requestTimeoutMs: Number(process.env.TURN_TIMEOUT_MS || 600000),
  })
  const value = { harness, fingerprint, running: false }
  sessions.set(sessionId, value)
  return value
}

async function disposeSession(sessionId, entry) {
  if (sessions.get(sessionId) === entry) sessions.delete(sessionId)
  const existing = closingSessions.get(sessionId)
  if (existing) return await existing
  const closing = entry.harness.close().catch(() => {})
  closingSessions.set(sessionId, closing)
  try {
    await closing
  } finally {
    if (closingSessions.get(sessionId) === closing) closingSessions.delete(sessionId)
  }
}

function sendEvent(res, type, payload) {
  res.write(`event: ${type}\ndata: ${JSON.stringify(payload)}\n\n`)
}

function blocksText(content) {
  if (!Array.isArray(content)) return ''
  return content.filter(block => block?.type === 'text').map(block => block.text || '').join('')
}

function parsedJson(value, fallback = value) {
  if (typeof value !== 'string') return value
  try { return JSON.parse(value) } catch { return fallback }
}

function toolResultEnvelope(data) {
  // The locked Harness revision persists the current result shape as a
  // ToolResultMessage containing one nested `tool-result` block.  Keep the
  // legacy flat shape readable so sessions created by earlier preview builds
  // remain recoverable after an adapter upgrade.
  const message = data.message
  const block = Array.isArray(message?.content)
    ? message.content.find(item => item?.type === 'tool-result')
    : null
  if (block) {
    const blockError = block.isError ? blocksText(block.content) : ''
    return {
      callId: String(block.toolCallId || message?.source?.callId || ''),
      content: block.content,
      isError: Boolean(block.isError || data.error),
      error: data.error || normalizePublicError(blockError) || null,
    }
  }
  return {
    callId: String(data.callId || ''),
    content: data.content,
    isError: Boolean(data.isError || data.error),
    error: data.error || null,
  }
}

function normalizePublicError(value) {
  if (!value) return ''
  if (typeof value === 'string') {
    return value.replace(/^Error:\s*/i, '').replace(/\[object Object\](?:,\[object Object\])*/g, '请求参数不符合结构化查询协议').slice(0, 1000)
  }
  if (Array.isArray(value)) return value.slice(0, 5).map(normalizePublicError).filter(Boolean).join('；')
  if (typeof value === 'object') {
    return normalizePublicError(value.message || value.msg || value.detail || value.code || value.name)
  }
  return String(value).slice(0, 1000)
}

function safeToolArguments(name, value) {
  const args = parsedJson(value, {}) || {}
  if (name !== 'structured_execute_query') return args
  return {
    mapping_version_id: args.mapping_version_id,
    max_rows: args.max_rows,
    original_question: args.semantic_query_plan?.original_question,
    entity_ids: args.semantic_query_plan?.entity_ids || [],
    relationship_ids: args.semantic_query_plan?.relationship_ids || [],
    output_count: args.semantic_query_plan?.outputs?.length || 0,
  }
}

function evidenceSatisfied(turnState) {
  return [...turnState.requiredTools].every(name => turnState.satisfiedTools.has(name))
}

function normalizeEvent(event, toolStarts, turnState) {
  const data = event?.data || {}
  switch (event?.type) {
    case 'turn/start': {
      turnState.satisfiedTools.clear()
      turnState.suppressedAnswer = false
      turnState.startedAt = Date.now()
      return [['turn_started', {
        turn: data.turn,
        has_prior_turns: typeof data.turn === 'number' ? data.turn > 1 : null,
        harness_seq: event.seq,
      }]]
    }
    case 'step/start': return [['step_started', { turn: data.turn, step: data.step, harness_seq: event.seq }]]
    case 'tool/call': {
      toolStarts.set(String(data.callId), { at: Date.now(), name: data.name })
      const payload = {
        call_id: data.callId,
        name: data.name,
        arguments: safeToolArguments(data.name, data.arguments),
        harness_seq: event.seq,
      }
      if (data.name === 'knowledge_search') return [['tool_started', payload], ['retrieval_started', payload]]
      if (data.name === 'structured_schema_search') {
        return [['tool_started', payload], ['structured_schema_search_started', payload]]
      }
      if (data.name === 'structured_execute_query') {
        return [
          ['tool_started', payload],
          ['structured_plan_started', payload],
          ['structured_query_started', payload],
        ]
      }
      return [['tool_started', payload]]
    }
    case 'tool/result': {
      const envelope = toolResultEnvelope(data)
      const started = toolStarts.get(envelope.callId)
      const text = blocksText(envelope.content)
      const structured = parsedJson(text, null)
      const payload = {
        call_id: envelope.callId,
        name: started?.name,
        success: !envelope.isError,
        error: normalizePublicError(envelope.error),
        duration_ms: started ? Date.now() - started.at : null,
        result: structured,
        harness_seq: event.seq,
      }
      toolStarts.delete(envelope.callId)
      if (!envelope.isError && started?.name) turnState.satisfiedTools.add(started.name)
      if (started?.name === 'knowledge_search' && structured) {
        return [['tool_finished', payload], ['retrieval_ranked', payload]]
      }
      if (started?.name === 'structured_schema_search') {
        return [['tool_finished', payload], ['structured_schema_search_finished', {
          ...payload,
          result_count: structured?.semantic_objects?.length || 0,
          mapping_versions: structured?.mapping_versions || [],
        }]]
      }
      if (started?.name === 'structured_get_object' && !envelope.isError) {
        return [['tool_finished', payload], ['structured_object_loaded', {
          ...payload,
          semantic_object_id: structured?.semantic_object?.id,
          attribute_count: structured?.attributes?.length || 0,
          relationship_count: structured?.relationships?.length || 0,
        }]]
      }
      if (started?.name === 'structured_execute_query') {
        if (envelope.isError) {
          const type = String(envelope.error || '').toLowerCase().includes('abort')
            ? 'structured_query_cancelled'
            : 'structured_query_failed'
          return [['tool_finished', payload], [type, payload]]
        }
        return [
          ['tool_finished', payload],
          ['structured_plan_validated', { ...payload, fingerprint: structured?.validation?.plan_fingerprint }],
          ['structured_ir_validated', { ...payload, fingerprint: structured?.validation?.ir_fingerprint }],
          ['structured_query_compiled', { ...payload, summary: structured?.safe_query_summary || {} }],
          ['structured_query_finished', {
            ...payload,
            query_run_id: structured?.query_run_id,
            row_count: structured?.row_count || 0,
            truncated: Boolean(structured?.truncated),
            elapsed_ms: structured?.elapsed_ms,
            source_citations: structured?.source_citations || [],
          }],
        ]
      }
      return [['tool_finished', payload]]
    }
    case 'assistant/chunk': {
      const chunk = data.chunk || {}
      if (chunk.type === 'text-delta') {
        // Do not leak a model's pre-evidence answer. The plugin will steer a
        // new step that performs knowledge_search; only post-search text is
        // streamed to the platform projection.
        if (!evidenceSatisfied(turnState)) {
          turnState.suppressedAnswer = true
          return null
        }
        return [['answer_delta', { text: chunk.text || '', harness_seq: event.seq }]]
      }
      return null
    }
    case 'turn/end': {
      const reason = data.reason && typeof data.reason === 'object'
        ? String(data.reason.kind || 'completed')
        : String(data.reason || 'completed')
      if (['aborted', 'interrupted', 'disposed'].includes(reason)) {
        const cancelled = [['turn_cancelled', {
          reason,
          duration_ms: turnState.startedAt ? Date.now() - turnState.startedAt : null,
          harness_seq: event.seq,
        }]]
        if ([...toolStarts.values()].some(item => item.name === 'structured_execute_query')) {
          cancelled.unshift(['structured_query_cancelled', { reason, harness_seq: event.seq }])
        }
        return cancelled
      }
      if (!evidenceSatisfied(turnState)) {
        return [['turn_failed', {
          reason: 'evidence-tools-required',
          code: 'EVIDENCE_TOOLS_REQUIRED',
          missing_tools: [...turnState.requiredTools].filter(name => !turnState.satisfiedTools.has(name)),
          duration_ms: turnState.startedAt ? Date.now() - turnState.startedAt : null,
          harness_seq: event.seq,
        }]]
      }
      if (['failed', 'error', 'blocked'].includes(reason)) {
        return [['turn_failed', {
          reason,
          duration_ms: turnState.startedAt ? Date.now() - turnState.startedAt : null,
          harness_seq: event.seq,
        }]]
      }
      const completed = [['turn_completed', {
        reason,
        duration_ms: turnState.startedAt ? Date.now() - turnState.startedAt : null,
        harness_seq: event.seq,
      }]]
      if (reason === 'max-tokens') {
        completed.unshift(['warning', { message: '回答达到模型输出上限', harness_seq: event.seq }])
      }
      return completed
    }
    default: return null
  }
}

async function runTurn(req, res, sessionId) {
  const input = await body(req)
  if (typeof input.content !== 'string' || !input.content.trim()) return json(res, 422, { detail: '消息不能为空' })
  const entry = await harnessFor(sessionId)
  if (entry.running) return json(res, 409, { detail: '该会话正在生成' })
  entry.running = true
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  })
  const toolStarts = new Map()
  const turnState = {
    requiredTools: new Set(evidenceRequirements(input.content)),
    satisfiedTools: new Set(),
    suppressedAnswer: false,
  }
  let completedSeen = false
  try {
    const result = await entry.harness.run(input.content.trim(), {
      sessionId,
      onNotification(notification) {
        if (notification.method !== 'session.event' || notification.params?.sessionId !== sessionId) return
        const event = notification.params.event
        const normalized = normalizeEvent(event, toolStarts, turnState)
        if (!normalized) return
        const occurredAt = new Date().toISOString()
        for (const [type, payload] of normalized) {
          if (type === 'turn_completed' || type === 'turn_cancelled' || type === 'turn_failed') completedSeen = true
          sendEvent(res, type, { ...payload, occurred_at: occurredAt, raw_type: event.type })
        }
      },
    })
    if (!completedSeen) sendEvent(res, 'turn_completed', {
      final_response: result.finalResponse,
      duration_ms: turnState.startedAt ? Date.now() - turnState.startedAt : null,
      occurred_at: new Date().toISOString(),
    })
  } catch (error) {
    sendEvent(res, 'turn_failed', {
      code: error?.name || 'AGENT_RUNTIME_ERROR',
      message: String(error?.message || error).slice(0, 500),
      duration_ms: turnState.startedAt ? Date.now() - turnState.startedAt : null,
      occurred_at: new Date().toISOString(),
    })
    // A failure before turn/start means SDK initialization never completed.
    // Evict that client so retry creates a fresh bridge instead of reusing a
    // half-initialized child process or a stale persistence lock.
    if (!turnState.startedAt) await disposeSession(sessionId, entry)
  } finally {
    entry.running = false
    res.end()
  }
}

async function cancelSession(res, sessionId) {
  const entry = sessions.get(sessionId)
  if (!entry) return json(res, 200, { ok: true, status: 'idle' })
  await disposeSession(sessionId, entry)
  return json(res, 200, { ok: true, status: 'cancelled' })
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`)
    if (req.method === 'GET' && url.pathname === '/health/live') return json(res, 200, { status: 'ok' })
    if (req.method === 'GET' && url.pathname === '/health/ready') {
      serviceSecret()
      return json(res, 200, { status: 'ready', harness_commit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc' })
    }
    const turn = url.pathname.match(/^\/v1\/sessions\/([^/]+)\/turns$/)
    if (req.method === 'POST' && turn) return await runTurn(req, res, decodeURIComponent(turn[1]))
    const cancel = url.pathname.match(/^\/v1\/sessions\/([^/]+)\/cancel$/)
    if (req.method === 'POST' && cancel) return await cancelSession(res, decodeURIComponent(cancel[1]))
    return json(res, 404, { detail: 'Not found' })
  } catch (error) {
    return json(res, 500, { detail: String(error?.message || error).slice(0, 500) })
  }
})

async function shutdown() {
  server.close()
  await Promise.all([...sessions.values()].map(entry => entry.harness.close().catch(() => {})))
  process.exit(0)
}

process.once('SIGTERM', shutdown)
process.once('SIGINT', shutdown)
server.listen(PORT, '0.0.0.0')
