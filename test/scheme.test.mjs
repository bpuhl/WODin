// node --test test/
//
// Scored sections (#37), driven by the three sessions the programming
// agent wrote as test cases: a rounds-for-time couplet, an AMRAP and a
// 21-15-9 ladder (examples/scored-*.json).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  expandRounds, roundCount, schemeProblems, scoreShape, buildScore,
  schemeLabel, scoreText, whiteboardLine
} from '../src/scheme.js';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const example = name => JSON.parse(readFileSync(path.join(ROOT, 'examples', `scored-${name}.json`), 'utf8'));
const metcon = name => example(name).sections[0];

test('a single set is repeated for every round, as ordinary positional sets', () => {
  const sec = expandRounds(metcon('for-time'));
  const [row, wallBall] = sec.exercises;
  assert.equal(row.sets.length, 3);
  assert.equal(wallBall.sets.length, 3);
  assert.deepEqual(wallBall.sets.map(s => s.reps), [15, 15, 15]);
  // No ids invented here: normalise() numbers them s1..s3 like any other set.
  assert.ok(wallBall.sets.every(s => s.id === undefined));
  // Copies, not the same object three times — editing a round must not edit all.
  assert.notEqual(wallBall.sets[0], wallBall.sets[1]);
});

test('expanding twice changes nothing', () => {
  const once = expandRounds(metcon('for-time'));
  const twice = expandRounds(structuredClone(once));
  assert.deepEqual(twice, once);
});

test('a ladder is sets as rungs: round count comes from them', () => {
  const sec = metcon('ladder');
  assert.equal(roundCount(sec), 3);
  expandRounds(sec);
  assert.deepEqual(sec.exercises[0].sets.map(s => s.reps), [21, 15, 9]);
});

test('an amrap has no round count and is never expanded', () => {
  const sec = expandRounds(metcon('amrap'));
  assert.equal(roundCount(sec), null);
  assert.ok(sec.exercises.every(ex => ex.sets.length === 1));
});

test('explicit set ids survive expansion with a round suffix', () => {
  const sec = { scheme: { kind: 'for_time', rounds: 2 },
                exercises: [{ movement: 'Row', sets: [{ id: 'pull', distance: 300 }] }] };
  assert.deepEqual(expandRounds(sec).exercises[0].sets.map(s => s.id), ['pull-r1', 'pull-r2']);
});

test('the three samples are valid', () => {
  for (const name of ['for-time', 'amrap', 'ladder']) {
    assert.deepEqual(schemeProblems(metcon(name)), [], name);
  }
});

test('set counts that are neither one nor one-per-round are refused', () => {
  const sec = { scheme: { kind: 'for_time', rounds: 3 },
                exercises: [{ movement: 'Row', sets: [{}, {}] }] };
  assert.match(schemeProblems(sec)[0], /2 sets in a 3-round section/);
});

test('an amrap needs a cap, no rounds, and one set per exercise', () => {
  const sec = { scheme: { kind: 'amrap', rounds: 5 },
                exercises: [{ movement: 'Sit-up', sets: [{}, {}] }] };
  const problems = schemeProblems(sec).join('\n');
  assert.match(problems, /needs a cap/);
  assert.match(problems, /has no rounds/);
  assert.match(problems, /exactly one set/);
});

test('unknown kinds and malformed durations are named', () => {
  assert.match(schemeProblems({ scheme: { kind: 'emom' }, exercises: [] })[0], /not one of/);
  assert.match(schemeProblems({ scheme: { kind: 'for_time', cap: '12 min' }, exercises: [] })[0], /not mm:ss/);
});

test('the score shape follows the kind', () => {
  assert.equal(scoreShape({ kind: 'for_time' }), 'time');
  assert.equal(scoreShape({ kind: 'amrap' }), 'rounds_reps');
  assert.equal(scoreShape({ kind: 'intervals' }), null);
  assert.equal(scoreShape(undefined), null);
});

test('for time: a time, or rounds + reps when capped', () => {
  const ft = { kind: 'for_time', rounds: 3 };
  assert.deepEqual(buildScore(ft, { time: '9:08' }), { time: '9:08', timeSec: 548 });
  assert.deepEqual(buildScore(ft, { time: '9:08', capped: true, rounds: '2', reps: '8' }),
                   { capped: true, rounds: 2, reps: 8 });
});

test('amrap: rounds + reps, with blank reps meaning a whole number of rounds', () => {
  const am = { kind: 'amrap', cap: '12:00' };
  assert.deepEqual(buildScore(am, { rounds: '5', reps: '12' }), { rounds: 5, reps: 12 });
  assert.deepEqual(buildScore(am, { rounds: '5', reps: '' }), { rounds: 5, reps: 0 });
});

test('nothing entered is no score — unanswered, never zero', () => {
  assert.equal(buildScore({ kind: 'for_time' }, {}), null);
  assert.equal(buildScore({ kind: 'for_time' }, { time: '' }), null);
  assert.equal(buildScore({ kind: 'amrap', cap: '12:00' }, { rounds: '', reps: '' }), null);
  assert.equal(buildScore({ kind: 'amrap', cap: '12:00' }, undefined), null);
  assert.equal(buildScore({ kind: 'intervals', rounds: 4 }, { time: '9:00' }), null);
});

test('the whiteboard reads like the wall', () => {
  const units = { load: 'lb', distance: 'm' };
  const ft = expandRounds(metcon('for-time'));
  assert.equal(schemeLabel(ft), '3 rounds for time');
  assert.deepEqual(ft.exercises.map(ex => whiteboardLine(ex, units)),
                   ['300 m Row @ 2:15/500m', '15 Wall ball @ 25 lb']);

  const ladder = metcon('ladder');
  assert.equal(schemeLabel(ladder), 'For time · cap 15:00');
  assert.equal(whiteboardLine(ladder.exercises[0], units), '21-15-9 Dumbbell Romanian deadlift @ 25 lb');

  const amrap = metcon('amrap');
  assert.equal(schemeLabel(amrap), 'AMRAP 12:00');
  assert.equal(whiteboardLine(amrap.exercises[1], units), '12 Ring row');

  assert.equal(schemeLabel({ scheme: { kind: 'intervals', rounds: 4, rest: '1:00' }, exercises: [] }),
               '4 rounds · rest 1:00');
});

test('scores read as a person would say them', () => {
  assert.equal(scoreText({ time: '9:08', timeSec: 548 }), '9:08');
  assert.equal(scoreText({ rounds: 5, reps: 12 }), '5 + 12');
  assert.equal(scoreText({ capped: true, rounds: 2, reps: 8 }), 'capped · 2 + 8');
  assert.equal(scoreText(null), '');
});
