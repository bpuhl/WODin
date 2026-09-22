import { test } from 'node:test';
import assert from 'node:assert/strict';
import { neighbours, mostRecent } from '../src/days.js';

const DATES = ['2026-09-14', '2026-09-16', '2026-09-17', '2026-09-20'];

test('steps over rest days, not to yesterday', () => {
  // The whole point: 09-16's previous is 09-14, not 09-15.
  assert.deepEqual(neighbours(DATES, '2026-09-16'),
                   { prev: '2026-09-14', next: '2026-09-17' });
});

test('no next at the newest, no prev at the oldest', () => {
  assert.equal(neighbours(DATES, '2026-09-20').next, null);
  assert.equal(neighbours(DATES, '2026-09-14').prev, null);
});

test('a date with no workout still offers both sides', () => {
  // Signed in, nothing today: the last session is still reachable.
  assert.deepEqual(neighbours(DATES, '2026-09-18'),
                   { prev: '2026-09-17', next: '2026-09-20' });
});

test('a date after everything offers only prev', () => {
  assert.deepEqual(neighbours(DATES, '2026-12-25'),
                   { prev: '2026-09-20', next: null });
});

test('an empty list offers nothing rather than throwing', () => {
  assert.deepEqual(neighbours([], '2026-09-20'), { prev: null, next: null });
});

test('unsorted and duplicated input is handled', () => {
  const messy = ['2026-09-20', '2026-09-14', '2026-09-20', '2026-09-16'];
  assert.deepEqual(neighbours(messy, '2026-09-16'),
                   { prev: '2026-09-14', next: '2026-09-20' });
});

test('mostRecent finds the last session on or before today', () => {
  assert.equal(mostRecent(DATES, '2026-09-22'), '2026-09-20');
  assert.equal(mostRecent(DATES, '2026-09-17'), '2026-09-17');
  assert.equal(mostRecent(DATES, '2026-09-01'), null);
});
