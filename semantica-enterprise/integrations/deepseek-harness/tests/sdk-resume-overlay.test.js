import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { patchSdkServer } from '../patch-sdk-server.mjs'


test('SDK bridge overlay resumes a persisted session and fails closed on drift', () => {
  const target = '/opt/deepseek-harness/packages/sdk/server/src/server.ts'
  const source = readFileSync(target, 'utf8')
  const patched = patchSdkServer(source)
  assert.match(patched, /ctx\.agents\.resume\(\{/)
  assert.match(patched, /SessionPersistenceNotFoundError/)
  assert.match(patched, /persistence\.inspect\(id\)/)
  assert.throws(() => patchSdkServer('unreviewed upstream source'), /no longer matches/)
})

