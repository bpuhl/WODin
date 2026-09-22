// Helpers that exist but are never called.
//
// dayNavHtml() was written, styled, tested at the logic level, deployed —
// and never invoked. The navigation could not have appeared however many
// workouts existed, and nothing failed: the function was simply dead. An
// edit that was supposed to insert the call into the header silently
// matched nothing.
//
// Unit tests cannot see this. They import the helper directly and it works
// perfectly in isolation, which is exactly the problem.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const SRC = path.join(ROOT, 'src');

function sources() {
  return readdirSync(SRC)
    .filter(f => f.endsWith('.js'))
    .map(f => [f, readFileSync(path.join(SRC, f), 'utf8')]);
}

test('every *Html() helper is actually called somewhere', () => {
  const all = sources();
  const combined = all.map(([, s]) => s).join('\n');

  for (const [file, src] of all) {
    for (const m of src.matchAll(/^function (\w+Html)\s*\(/gm)) {
      const name = m[1];
      // Any reference, not just a call: a helper can legitimately be
      // passed by name, as .map(renderEx) does. Requiring "name(" flagged
      // that as dead, which would have taught everyone to ignore this test.
      const refs = [...combined.matchAll(new RegExp(`\\b${name}\\b`, 'g'))].length;
      assert.ok(refs > 1,
        `${file}: ${name}() is defined but never called — it will render ` +
        `nothing, and no test will notice because the helper itself works`);
    }
  }
});

test('every render function is reachable from somewhere', () => {
  const all = sources();
  const combined = all.map(([, s]) => s).join('\n');

  for (const [file, src] of all) {
    for (const m of src.matchAll(/^(?:async )?function (render\w+)\s*\(/gm)) {
      const name = m[1];
      const refs = [...combined.matchAll(new RegExp(`\\b${name}\\b`, 'g'))].length;
      assert.ok(refs > 1, `${file}: ${name}() is defined but never referenced`);
    }
  }
});
