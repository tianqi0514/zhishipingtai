import { createServer } from 'node:http'
import { readFileSync } from 'node:fs'
import { DeepSeekHarness } from '/opt/deepseek-harness/packages/sdk/client/src/index.ts'
import { requiresKnowledgeSearch } from './query-policy.js'

const PORT = Number(process.env.PORT || 8090)
const PLATFORM_API = (process.env.PLATFORM_API || 'http://api:8080/api/v1').replace(/\/$/, '')
const SERVICE_SECRET_FILE = process.env.AGENT_SERVICE_SECRET_FILE || '/run/secrets/agent_service_secret'
const DSH_HOME = process.env.DSH_HOME || '/var/lib/chuanshen-harness'
const PATCH = process.env.DSH_PATCH || '/opt/deepseek-harness/packages/integration/chuanshen-knowledge/cordis.patch.yml'
const sessions = new Map()

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
    initializeTimeoutMs: 30000,
    requestTimeoutMs: Number(process.env.TURN_TIMEOUT_MS || 600000),
  })
  const value = { harness, fingerprint, running: false }
  sessions.set(sessionId, value)
  return value
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
    return {
      callId: String(block.toolCallId || message?.source?.callId || ''),
      content: block.content,
      isError: Boolean(block.isError || data.error),
      error: data.error || null,
    }
  }
  return {
    callId: String(data.callId || ''),
    content: data.content,
    isError: Boolean(data.isError || data.error),
    error: data.error || null,
  }
}

function normalizeEvent(event, toolStarts, turnState) {
  const data = event?.data || {}
  switch (event?.type) {
    case 'turn/start': {
      turnState.hasSearch = false
      turnState.suppressedAnswer = false
      return [['turn_started', { turn: data.turn, harness_seq: event.seq }]]
    }
    case 'step/start': return [['step_started', { turn: data.turn, step: data.step, harness_seq: event.seq }]]
    case 'tool/call': {
      toolStarts.set(String(data.callId), { at: Date.now(), name: data.name })
      if (data.name === 'knowledge_search') turnState.hasSearch = true
      const payload = {
        call_id: data.callId,
        name: data.name,
        arguments: parsedJson(data.arguments, {}),
        harness_seq: event.seq,
      }
      return data.name === 'knowledge_search'
        ? [['tool_started', payload], ['retrieval_started', payload]]
        : [['tool_started', payload]]
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
        error: envelope.error,
        duration_ms: started ? Date.now() - started.at : null,
        result: structured,
        harness_seq: event.seq,
      }
      toolStarts.delete(envelope.callId)
      return started?.name === 'knowledge_search' && structured
        ? [['tool_finished', payload], ['retrieval_ranked', payload]]
        : [['tool_finished', payload]]
    }
    case 'assistant/chunk': {
      const chunk = data.chunk || {}
      if (chunk.type === 'text-delta') {
        // Do not leak a model's pre-evidence answer. The plugin will steer a
        // new step that performs knowledge_search; only post-search text is
        // streamed to the platform projection.
        if (turnState.requiresSearch && !turnState.hasSearch) {
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
        return [['turn_cancelled', { reason, harness_seq: event.seq }]]
      }
      if (turnState.requiresSearch && !turnState.hasSearch) {
        return [['turn_failed', {
          reason: 'knowledge-search-required',
          code: 'KNOWLEDGE_SEARCH_REQUIRED',
          harness_seq: event.seq,
        }]]
      }
      if (['failed', 'error', 'blocked'].includes(reason)) {
        return [['turn_failed', { reason, harness_seq: event.seq }]]
      }
      const completed = [['turn_completed', { reason, harness_seq: event.seq }]]
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
    hasSearch: false,
    suppressedAnswer: false,
    requiresSearch: requiresKnowledgeSearch(input.content),
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
        for (const [type, payload] of normalized) {
          if (type === 'turn_completed' || type === 'turn_cancelled' || type === 'turn_failed') completedSeen = true
          sendEvent(res, type, { ...payload, raw_type: event.type })
        }
      },
    })
    if (!completedSeen) sendEvent(res, 'turn_completed', { final_response: result.finalResponse })
  } catch (error) {
    sendEvent(res, 'turn_failed', { code: error?.name || 'AGENT_RUNTIME_ERROR', message: String(error?.message || error).slice(0, 500) })
  } finally {
    entry.running = false
    res.end()
  }
}

async function cancelSession(res, sessionId) {
  const entry = sessions.get(sessionId)
  if (!entry) return json(res, 200, { ok: true, status: 'idle' })
  await entry.harness.close()
  sessions.delete(sessionId)
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
