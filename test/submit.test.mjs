import { test } from 'node:test';
import assert from 'node:assert/strict';
import { hasSink, sheetActions } from '../src/submit.js';

test('a published workout posts directly', () => {
  assert.equal(hasSink({ sink: { type: 'post', url: 'https://wod.imav8n.com/api/log' } }), true);
});

test('a shared link with no sink falls back to the sheet', () => {
  // The case upstream's four-button sheet exists for, and it must keep working.
  for (const w of [{}, null, { sink: {} }, { sink: { type: 'post' } },
                   { sink: { url: 'x' } }, { sink: { type: 'blind', url: 'x' } }]) {
    assert.equal(hasSink(w), false, JSON.stringify(w));
  }
});

test('the sheet never re-offers the send that just failed', () => {
  assert.ok(!sheetActions({ canShare: true }).includes('post'));
  assert.ok(!sheetActions({ canShare: false }).includes('post'));
});

test('download is gone', () => {
  // Results live in the bucket; a file in a phone's downloads folder is a
  // worse place to read them from.
  assert.ok(!sheetActions({ canShare: true }).includes('download'));
});

test('copy is always available, share only when supported', () => {
  assert.deepEqual(sheetActions({ canShare: true }), ['share', 'copy']);
  assert.deepEqual(sheetActions({ canShare: false }), ['copy']);
});
