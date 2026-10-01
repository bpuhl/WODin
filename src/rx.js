/* The parts of a prescription the athlete reads but never fills: tempo,
 * rest and intensity.
 *
 * The schema carried all three from the start and the page drew none of
 * them (#36), so the programming agent wrote "3-sec lower" and "rest ~2
 * min" into the cue instead — which keeps them out of the data. They are
 * display only: nothing here is logged, and isAsPlanned never looks at
 * them.
 */

const FIELDS = [
  ['tempo', 'Tempo'],
  ['intensity', 'Intensity'],
  ['rest', 'Rest']
];

/* Rest is mm:ss, like every other duration in the schema. "90s" was the
 * schema's own example once, so it is still read: whole seconds become
 * m:ss. Anything else is shown exactly as written rather than guessed at. */
export function restLabel(value) {
  const s = String(value ?? '').trim();
  const secs = s.match(/^(\d+)\s*s(ec(onds?)?)?$/i);
  if (!secs) return s;
  const n = Number(secs[1]);
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
}

function display(field, value) {
  return field === 'rest' ? restLabel(value) : String(value).trim();
}

const present = v => v !== null && v !== undefined && String(v).trim() !== '';

/* Where each value is shown.
 *
 * A value every prescribed set agrees on is said once, under the cue —
 * "Tempo 3-1-1-0 · Rest 2:00" — rather than repeated down every row.
 * One that varies is shown on the rows that carry it. Added sets are not
 * part of the prescription, so they neither count towards agreement nor
 * get a line of their own.
 *
 * Returns { shared: [[label, value]], perSet: { setId: [[label, value]] } }. */
export function prescriptionNotes(sets) {
  const planned = (sets || []).filter(s => !s._added);
  const shared = [];
  const perSet = {};

  for (const [field, label] of FIELDS) {
    const values = planned.map(s => present(s[field]) ? display(field, s[field]) : null);
    if (values.every(v => v === null)) continue;

    if (values.every(v => v === values[0])) {
      shared.push([label, values[0]]);
      continue;
    }
    planned.forEach((s, i) => {
      if (values[i] === null) return;
      (perSet[s.id] ||= []).push([label, values[i]]);
    });
  }
  return { shared, perSet };
}
