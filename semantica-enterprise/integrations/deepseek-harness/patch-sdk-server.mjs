import { readFileSync, writeFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'


const IMPORT_ANCHOR = "import type { Agent, AgentHandle } from '@deepseek-ai/dsh-agent'"
const IMPORT_REPLACEMENT = `${IMPORT_ANCHOR}\nimport { SessionPersistenceNotFoundError } from '@deepseek-ai/dsh-session-persistence'`

const CREATE_SESSION_ANCHOR = `  private async createSession(sessionId: string): Promise<SessionRecord> {
    // No preset composition: this server's compositions keep the model-facing
    // rows in the host plane, so this agent reads them from the global layer. A
    // deployment that configures a roster has to join one here first
    // (@deepseek-ai/dsh-agent-presets README, "Composing a child agent").
    const handle = await this.ctx.agents.create({
      sessionId: SessionId(sessionId),
      meta: { cwd: this.cwd },
      agentOptions: {
        provider: this.provider,
        model: this.model,
        ...this.reasoningEffort === undefined ? {} : { reasoningEffort: this.reasoningEffort },
        ...this.maxTokens === undefined ? {} : { maxTokens: this.maxTokens },
      },
    })
    const rec: SessionRecord = { handle }
    this.sessions.set(sessionId, rec)
    return rec
  }`

const CREATE_SESSION_REPLACEMENT = `  private async createSession(sessionId: string): Promise<SessionRecord> {
    // The locked Developer Preview SDK server creates an empty session for an
    // unknown in-process id.  After a runtime restart that would silently
    // shadow an existing JSONL log and drop the model context.  Resume through
    // Harness' public Agent registry when an exact persisted identity exists;
    // only a genuine not-found result is allowed to create a fresh session.
    const id = SessionId(sessionId)
    const agentOptions = {
      provider: this.provider,
      model: this.model,
      ...this.reasoningEffort === undefined ? {} : { reasoningEffort: this.reasoningEffort },
      ...this.maxTokens === undefined ? {} : { maxTokens: this.maxTokens },
    }
    const persistence = this.ctx.get('sessionPersistence')
    let handle: AgentHandle | undefined
    if (persistence !== undefined) {
      try {
        await persistence.inspect(id)
        handle = await this.ctx.agents.resume({
          resumeSessionId: id,
          agentOptions,
        })
      } catch (error) {
        if (!(error instanceof SessionPersistenceNotFoundError)) throw error
      }
    }
    handle ??= await this.ctx.agents.create({
      sessionId: id,
      meta: { cwd: this.cwd },
      agentOptions,
    })
    const rec: SessionRecord = { handle }
    this.sessions.set(sessionId, rec)
    return rec
  }`


export function patchSdkServer(source) {
  if (source.includes('Resume through\n    // Harness\' public Agent registry')) return source
  if (!source.includes(IMPORT_ANCHOR) || !source.includes(CREATE_SESSION_ANCHOR)) {
    throw new Error('Locked Harness SDK server no longer matches the reviewed resume overlay')
  }
  return source
    .replace(IMPORT_ANCHOR, IMPORT_REPLACEMENT)
    .replace(CREATE_SESSION_ANCHOR, CREATE_SESSION_REPLACEMENT)
}


if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const target = process.argv[2]
  if (!target) throw new Error('Usage: node patch-sdk-server.mjs <server.ts>')
  const source = readFileSync(target, 'utf8')
  const patched = patchSdkServer(source)
  writeFileSync(target, patched)
}

