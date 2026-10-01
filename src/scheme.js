/* Scored sections (#37): a section whose exercises are done together, in
 * rounds, and scored as one.
 *
 *   "scheme": { "kind": "for_time", "rounds": 3 }
 *   "scheme": { "kind": "amrap", "cap": "12:00" }
 *   "scheme": { "kind": "intervals", "rounds": 4, "rest": "1:00" }
 *
 * Set K is round K. An exercise has one set, repeated every round, or
 * exactly one set per round — which is how a 21-15-9 ladder is written,
 * with no ladder kind: each rung is a set. Expanding the single-set case
 * here, before anything else sees the plan, means every round is an
 * ordinary set downstream: it seeds, logs, compares and reads back in
 * history exactly as before, and the movements stay in movement history.
 *
 * The score's shape follows from the kind. There is deliberately no
 * separate score field to disagree with it.
 */

export const SCHEME_KINDS = ['for_time', 'amrap', 'intervals'];

const MMSS = /^\d{1,3}:[0-5]\d$/;

/* How many rounds a section has. null for an AMRAP — that is the score. */
export function roundCount(sec) {
  const s = sec && sec.scheme;
  if (!s || s.kind === 'amrap') return null;
  if (s.rounds) return s.rounds;
  const lengths = (sec.exercises || []).map(ex => (ex.sets || []).length);
  return Math.max(1, ...lengths);
}

/* Repeat single-set exercises across the rounds. Ids follow the same
 * positional rule as every other set (s1, s2 …), so a result reads back
 * against the plan without knowing a scheme was involved. */
export function expandRounds(sec) {
  const n = roundCount(sec);
  if (!n) return sec;
  (sec.exercises || []).forEach(ex => {
    if ((ex.sets || []).length !== 1 || n === 1) return;
    const [only] = ex.sets;
    ex.sets = Array.from({ length: n }, (_, i) => {
      const copy = { ...only };
      delete copy.id;
      if (only.id) copy.id = `${only.id}-r${i + 1}`;
      return copy;
    });
  });
  return sec;
}

/* Everything JSON Schema cannot say about a scheme. Used by `wodin
 * validate`; the page renders what it can regardless. */
export function schemeProblems(sec) {
  const s = sec && sec.scheme;
  if (!s) return [];
  const out = [];
  if (!SCHEME_KINDS.includes(s.kind)) {
    out.push(`scheme.kind "${s.kind}" is not one of ${SCHEME_KINDS.join(', ')}`);
    return out;
  }
  if (s.cap != null && !MMSS.test(String(s.cap))) out.push(`scheme.cap "${s.cap}" is not mm:ss`);
  if (s.rest != null && !MMSS.test(String(s.rest))) out.push(`scheme.rest "${s.rest}" is not mm:ss`);
  if (s.kind === 'amrap') {
    if (!s.cap) out.push('an amrap needs a cap ("12:00") — it is the whole prescription');
    if (s.rounds != null) out.push('an amrap has no rounds — rounds is what the athlete scores');
    (sec.exercises || []).forEach(ex => {
      if ((ex.sets || []).length !== 1) {
        out.push(`${ex.movement}: an amrap exercise has exactly one set, the round's prescription`);
      }
    });
    return out;
  }
  const n = roundCount(sec);
  (sec.exercises || []).forEach(ex => {
    const len = (ex.sets || []).length;
    if (len !== 1 && len !== n) {
      out.push(`${ex.movement}: ${len} sets in a ${n}-round section — give one set (repeated each round) or exactly ${n}, one per round`);
    }
  });
  return out;
}

/* What the athlete enters for the section as a whole. */
export function scoreShape(scheme) {
  if (!scheme) return null;
  if (scheme.kind === 'for_time') return 'time';
  if (scheme.kind === 'amrap') return 'rounds_reps';
  return null;
}

const toSec = str => {
  const p = String(str).split(':').map(Number);
  if (!str || p.some(isNaN)) return null;
  return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2]
       : p.length === 2 ? p[0] * 60 + p[1] : p[0];
};
const int = v => (v === '' || v == null || isNaN(Number(v))) ? null : Math.max(0, Math.round(Number(v)));

/* The score as it goes into the result, or null when nothing was entered.
 * Unanswered is absent, never zero — the same rule as RPE.
 *
 * for_time → { time, timeSec }, or { capped, rounds, reps } when the cap
 *            beat the athlete.
 * amrap    → { rounds, reps }. Reps left blank with rounds given is a
 *            whole number of rounds, so it is 0, not unanswered. */
export function buildScore(scheme, input) {
  const v = input || {};
  const shape = scoreShape(scheme);
  if (!shape) return null;

  if (shape === 'time' && !v.capped) {
    const timeSec = toSec(v.time);
    return timeSec == null ? null : { time: String(v.time), timeSec };
  }
  const rounds = int(v.rounds);
  const reps = int(v.reps);
  if (rounds == null && reps == null) return null;
  const score = { rounds: rounds ?? 0, reps: reps ?? 0 };
  return shape === 'time' ? { capped: true, ...score } : score;
}

/* One line for the whiteboard: "3 rounds for time", "AMRAP 12:00". */
export function schemeLabel(sec) {
  const s = sec.scheme;
  const n = roundCount(sec);
  const rest = s.rest ? ` · rest ${s.rest}` : '';
  if (s.kind === 'amrap') return `AMRAP ${s.cap || ''}`.trim();
  if (s.kind === 'for_time') {
    const head = s.rounds > 1 ? `${s.rounds} rounds for time` : 'For time';
    return head + (s.cap ? ` · cap ${s.cap}` : '') + rest;
  }
  return `${n} round${n === 1 ? '' : 's'}${rest}`;
}

/* The score as a person reads it: "9:08", "5 + 12", "capped · 2 + 8". */
export function scoreText(score) {
  if (!score) return '';
  if (score.time) return score.time;
  const rr = `${score.rounds ?? 0} + ${score.reps ?? 0}`;
  return score.capped ? `capped · ${rr}` : rr;
}

/* How each movement reads on the whiteboard — "15 Wall ball @ 25 lb",
 * "21-15-9 Dumbbell RDL @ 25 lb". Values that change round to round are
 * joined with dashes, which is how a ladder is written on any gym wall.
 * Safe to call after expandRounds: a repeated value collapses to one. */
export function whiteboardLine(ex, units) {
  const sets = ex.sets || [];
  const kind = ex.kind || 'weight_reps';
  const join = (field, fmt = v => v) => {
    const vals = sets.map(s => s[field]).filter(v => v != null && v !== '');
    if (!vals.length) return '';
    const uniq = [...new Set(vals.map(String))];
    return uniq.length === 1 ? fmt(uniq[0]) : vals.map(String).join('-');
  };
  const loadUnit = (units && units.load) || 'lb';
  const distUnit = (sets[0] && sets[0].distanceUnit) || (units && units.distance) || 'm';
  const bw = sets.every(s => s.loadType === 'bodyweight');
  const load = bw ? '' : join('load');
  const atLoad = load ? ` @ ${load} ${loadUnit}` : '';

  let lead = '';
  if (kind === 'cardio' || kind === 'carry') {
    const d = join('distance');
    lead = d ? `${d} ${distUnit}` : join('duration');
    if (kind === 'carry') return `${lead} ${ex.movement}${atLoad}`.trim();
    const pace = join('pace');
    return `${lead} ${ex.movement}${pace ? ` @ ${pace}` : ''}`.trim();
  }
  if (kind === 'time') return `${join('duration')} ${ex.movement}`.trim();
  return `${join('reps')} ${ex.movement}${kind === 'weight_reps' ? atLoad : ''}`.trim();
}
