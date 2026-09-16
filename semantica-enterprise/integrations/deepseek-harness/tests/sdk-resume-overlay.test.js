import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { patchSdkServer } from '../patch-sdk-server.mjs'


test('SDK bridge overlay resumes a persisted session and fails closed on drift', () => {
  const installed = '/opt/deepseek-harness/packages/sdk/server/src/server.ts'
  const target = existsSync(installed)
    ? installed
    : fileURLToPath(new URL('../../../../deepseek-harness/packages/sdk/server/src/server.ts', import.meta.url))
  const source = readFileSync(target, 'utf8')
  const patched = patchSdkServer(source)
  assert.match(patched, /ctx\.agents\.resume\(\{/)
  assert.match(patched, /SessionPersistenceNotFoundError/)
  assert.match(patched, /persistence\.inspect\(id\)/)
  assert.throws(() => patchSdkServer('unreviewed upstream source'), /no longer matches/)
})
