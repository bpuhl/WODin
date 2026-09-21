// node --test test/
//
// Covers the one function that decides a field in every stored result.
// Agents are told to filter on asPlanned to find where a session diverged,
// so a wrong answer here corrupts history silently rather than breaking
// anything visible.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isAsPlanned } from '../src/planned.js';

test('the regression: a cardio set the athlete times is not a deviation', () => {
  // Exactly the shape the agent published: distance and pace prescribed,
  // duration left for the athlete. Before the fix this always returned
  // false, so a perfectly executed interval session reported every set as
  // off-plan.
  const set = { distance: 250, pace: '2:15/500m', duration: null, athleteFills: 'duration' };
  const logged = { distance: 250, pace: '2:15', duration: '2:10', durationSec: 130 };
  assert.equal(isAsPlanned(set, logged, 'cardio'), true);
});

test('a cardio set at the wrong distance still is a deviation', () => {
  const set = { distance: 250, duration: null, athleteFills: 'duration' };
  assert.equal(isAsPlanned(set, { distance: 500, duration: '2:10' }, 'cardio'), false);
});

test('without athleteFills, cardio still compares duration', () => {
  const set = { distance: 500, duration: '2:00' };
  assert.equal(isAsPlanned(set, { distance: 500, duration: '2:00' }, 'cardio'), true);
  assert.equal(isAsPlanned(set, { distance: 500, duration: '2:11' }, 'cardio'), false);
});

test('weight_reps unaffected when nothing is athlete-filled', () => {
  const set = { load: 95, reps: 5 };
  assert.equal(isAsPlanned(set, { load: 95, reps: 5 }, 'weight_reps'), true);
  assert.equal(isAsPlanned(set, { load: 105, reps: 5 }, 'weight_reps'), false);
  assert.equal(isAsPlanned(set, { load: 95, reps: 3 }, 'weight_reps'), false);
});

test('an athlete-filled load is not a deviation', () => {
  // "work up to a heavy single" — the plan cannot know the number.
  const set = { load: null, reps: 1, athleteFills: 'load' };
  assert.equal(isAsPlanned(set, { load: 225, reps: 1 }, 'weight_reps'), true);
  assert.equal(isAsPlanned(set, { load: 225, reps: 3 }, 'weight_reps'), false,
    'reps were prescribed, so they still count');
});

test('several athlete-filled fields', () => {
  const set = { distance: null, duration: null, athleteFills: 'distance duration' };
  assert.equal(isAsPlanned(set, { distance: 400, duration: '1:40' }, 'cardio'), true);
});

test('an added set is always a deviation', () => {
  const set = { _added: true, reps: 5, athleteFills: 'reps' };
  assert.equal(isAsPlanned(set, { reps: 5 }, 'reps'), false);
});

test('bodyweight still resolves to BW', () => {
  const set = { loadType: 'bodyweight', reps: 10 };
  assert.equal(isAsPlanned(set, { load: 'BW', reps: 10 }, 'weight_reps'), true);
  assert.equal(isAsPlanned(set, { load: 45, reps: 10 }, 'weight_reps'), false);
});

test('time holds, and an athlete-filled hold', () => {
  assert.equal(isAsPlanned({ duration: '0:20' }, { duration: '0:20' }, 'time'), true);
  assert.equal(isAsPlanned({ duration: '0:20' }, { duration: '0:15' }, 'time'), false);
  assert.equal(isAsPlanned({ duration: null, athleteFills: 'duration' },
                           { duration: '0:47' }, 'time'), true,
    'max-effort hold: any time is the plan being followed');
});

test('carry compares load, reps and distance', () => {
  const set = { load: 50, reps: 1, distance: 100 };
  assert.equal(isAsPlanned(set, { load: 50, reps: 1, distance: 100 }, 'carry'), true);
  assert.equal(isAsPlanned(set, { load: 50, reps: 1, distance: 80 }, 'carry'), false);
});
