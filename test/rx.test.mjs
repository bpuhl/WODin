// node --test test/
//
// Tempo, rest and intensity were in the schema from the start and never
// drawn (#36). These decide where each one appears.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { prescriptionNotes, restLabel } from '../src/rx.js';

test('a value every set agrees on is said once, not per row', () => {
  const sets = [
    { id: 's1', reps: 8, tempo: '3-1-1-0', rest: '2:00' },
    { id: 's2', reps: 8, tempo: '3-1-1-0', rest: '2:00' }
  ];
  assert.deepEqual(prescriptionNotes(sets), {
    shared: [['Tempo', '3-1-1-0'], ['Rest', '2:00']],
    perSet: {}
  });
});

test('a value that varies goes on the rows that carry it', () => {
  const sets = [
    { id: 's1', intensity: '70%' },
    { id: 's2', intensity: '80%' },
    { id: 's3' }
  ];
  assert.deepEqual(prescriptionNotes(sets), {
    shared: [],
    perSet: { s1: [['Intensity', '70%']], s2: [['Intensity', '80%']] }
  });
});

test('nothing prescribed draws nothing', () => {
  assert.deepEqual(prescriptionNotes([{ id: 's1', reps: 5, tempo: null, rest: '' }]),
                   { shared: [], perSet: {} });
});

test('added sets neither break agreement nor get a line', () => {
  const sets = [
    { id: 's1', rest: '1:30' },
    { id: 's2', rest: '1:30' },
    { id: 'a1', _added: true }
  ];
  assert.deepEqual(prescriptionNotes(sets).shared, [['Rest', '1:30']]);
});

test('rest reads the old "90s" form as mm:ss and leaves the rest alone', () => {
  assert.equal(restLabel('90s'), '1:30');
  assert.equal(restLabel('45 sec'), '0:45');
  assert.equal(restLabel('2:00'), '2:00');
  assert.equal(restLabel('2-3 min'), '2-3 min');
});

test('"90s" and "1:30" on different sets still count as the same rest', () => {
  const sets = [{ id: 's1', rest: '90s' }, { id: 's2', rest: '1:30' }];
  assert.deepEqual(prescriptionNotes(sets).shared, [['Rest', '1:30']]);
});
