// node --test test/
//
// The programming agent checks one version number before every build:
// x-version in the schemas it validates against, and the version at the
// top of AGENT.md whose changelog says what changed. If those drift
// apart, the agent's "am I current?" check lies to it — so it is
// asserted here rather than left to whoever bumps one of them.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const read = (...p) => readFileSync(path.join(ROOT, ...p), 'utf8');

const guide = read('AGENT.md');
const guideVersion = (guide.match(/\*\*Protocol version (\d+\.\d+\.\d+)\*\*/) || [])[1];

test('AGENT.md states its protocol version', () => {
  assert.ok(guideVersion, 'no "**Protocol version x.y.z**" line at the top of AGENT.md');
});

test('both schemas carry the same version as the guide', () => {
  for (const name of ['wod.schema.json', 'result.schema.json']) {
    const schema = JSON.parse(read('schema', name));
    assert.equal(schema['x-version'], guideVersion, `${name} x-version`);
  }
});

test('the changelog has an entry for the current version', () => {
  const log = guide.slice(guide.indexOf('## Changelog'));
  assert.match(log, new RegExp(`\\*\\*${guideVersion.replace(/\./g, '\\.')}\\*\\*`),
               `no changelog entry for ${guideVersion}`);
});

test('the schema major matches the schema constants', () => {
  const major = guideVersion.split('.')[0];
  assert.equal(JSON.parse(read('schema', 'wod.schema.json')).properties.schema.const, `wodin/wod@${major}`);
  assert.equal(JSON.parse(read('schema', 'result.schema.json')).properties.schema.const, `wodin/result@${major}`);
});
