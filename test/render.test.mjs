// node --test test/
//
// `wodin render` writes one self-contained HTML file. It inlined main.js
// by deleting only the first import line, so once main.js imported a
// second module the page could not boot — and nothing noticed, because
// nothing ran it (#44). This renders a real plan and checks the inlined
// script is a single valid script with nothing left to import.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

function render(example) {
  const dir = mkdtempSync(path.join(tmpdir(), 'wodin-render-'));
  const out = path.join(dir, 'out.html');
  execFileSync(process.execPath, [path.join(ROOT, 'cli', 'wodin.mjs'), 'render',
    path.join(ROOT, 'examples', example), '-o', out], { stdio: 'pipe' });
  const html = readFileSync(out, 'utf8');
  return { dir, html };
}

const moduleScript = html => {
  const m = html.match(/<script type="module">\n([\s\S]*?)\n<\/script>/);
  assert.ok(m, 'no inline module script in the rendered page');
  return m[1];
};

test('the rendered page imports nothing', () => {
  const { dir, html } = render('scored-for-time.json');
  try {
    const js = moduleScript(html);
    assert.doesNotMatch(js, /^\s*import\s/m, 'an import survived inlining');
    assert.doesNotMatch(js, /^\s*export\s/m, 'an export survived inlining');
    assert.doesNotMatch(html, /src="src\//, 'the page still points at src/');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('the inlined script is valid JavaScript as one module', () => {
  // Catches what concatenation gets wrong: two modules declaring the same
  // top-level name (main.js and scheme.js both have toSec) is a
  // SyntaxError the moment they share a scope.
  const { dir, html } = render('scored-for-time.json');
  try {
    const file = path.join(dir, 'inline.mjs');
    writeFileSync(file, moduleScript(html));
    execFileSync(process.execPath, ['--check', file], { stdio: 'pipe' });
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('every module main.js reaches is inlined', () => {
  const { dir, html } = render('minimal.json');
  try {
    const js = moduleScript(html);
    for (const mod of ['icons', 'planned', 'submit', 'days', 'rx', 'scheme']) {
      assert.match(js, new RegExp(`__wodin\\["src/${mod}\\.js"\\] = `), `${mod}.js was not inlined`);
    }
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
