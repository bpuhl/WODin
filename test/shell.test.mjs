// Every module the app imports must be precached.
//
// planned.js was added to src/ and imported by main.js without being added
// to the worker's SHELL list. Non-shell paths are networkFirst, so after a
// deploy the worker activates a fresh cache that does not contain it, and
// an athlete opening the app with no signal gets a 503 for a module
// main.js imports -- the app does not boot at all. At a gym, which is the
// one place this app is for.
//
// Checking the list by eye is exactly the review that already failed once.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const read = (...p) => readFileSync(path.join(ROOT, ...p), 'utf8');

function shellList() {
  const sw = read('public', 'sw.js');
  const block = sw.slice(sw.indexOf('const SHELL'), sw.indexOf('].map'));
  return [...block.matchAll(/'([^']+)'/g)].map(m => m[1]);
}

/* Follow the import graph from the entry point, so a module imported by a
   module is caught too. */
function reachableModules(entry) {
  const seen = new Set();
  const queue = [entry];
  while (queue.length) {
    const rel = queue.shift();
    if (seen.has(rel)) continue;
    seen.add(rel);
    const src = read('src', path.basename(rel));
    for (const m of src.matchAll(/^\s*import\s[^'"]*['"]\.\/([^'"]+)['"]/gm)) {
      queue.push('src/' + m[1]);
    }
  }
  return seen;
}

test('every module reachable from main.js is precached', () => {
  const shell = shellList();
  for (const mod of reachableModules('src/main.js')) {
    assert.ok(shell.includes(mod),
      `${mod} is imported but missing from sw.js SHELL — the app will fail ` +
      `to boot offline after the next deploy`);
  }
});

test('every js and css file in src/ and styles/ is precached', () => {
  const shell = shellList();
  for (const dir of ['src', 'styles']) {
    for (const f of readdirSync(path.join(ROOT, dir))) {
      if (!/\.(js|css)$/.test(f)) continue;
      assert.ok(shell.includes(`${dir}/${f}`),
        `${dir}/${f} exists but is not in sw.js SHELL`);
    }
  }
});

test('every precached path actually exists in the built output', () => {
  // The mirror image: a typo'd entry makes cache.addAll reject and the
  // install fails wholesale rather than partially.
  //
  // Checked against dist/ rather than the repo, because the two do not
  // match -- manifest.webmanifest and the fonts live under public/ in the
  // source and at the root once built. dist/ is what the worker actually
  // fetches, so it is the only layout worth asserting against. (This test
  // got that wrong first time round, which rather makes the point.)
  const dist = path.join(ROOT, 'dist');
  if (!existsSync(dist)) {
    assert.fail('dist/ not built — run `node scripts/build.mjs` first');
  }
  for (const p of shellList()) {
    const target = p === '.' ? 'index.html' : p;
    assert.ok(existsSync(path.join(dist, target)),
      `sw.js precaches ${p}, absent from dist/ — cache.addAll rejects and ` +
      `the whole install fails`);
  }
});
