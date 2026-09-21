/* Was a set done as prescribed?
 *
 * Extracted from main.js so it can be tested. It decides a field in every
 * stored result, and an agent is told to filter on it to find where a
 * session diverged — so getting it wrong quietly corrupts the history
 * rather than breaking anything visible.
 */

/* Fields the plan explicitly leaves for the athlete to supply.
 * "athleteFills": "duration" — or a list, hence the split. */
function filledByAthlete(set) {
  return new Set(
    String(set.athleteFills ?? '')
      .split(/[,\s]+/)
      .filter(Boolean)
  );
}

export function isAsPlanned(set, v, kind) {
  if (set._added) return false;

  const fills = filledByAthlete(set);
  const same = (a, b) => String(a ?? '') === String(b ?? '');

  /* A field the plan asked the athlete to provide cannot disagree with the
   * plan: the plan holds null there on purpose. Comparing it marked every
   * such set as a deviation — so a cardio set prescribing distance and
   * pace with "athleteFills": "duration" could never come back
   * asPlanned: true, no matter how exactly it was performed. Agents are
   * told to read asPlanned: false as "this is where it diverged", so the
   * effect was to report every interval of a perfectly executed session
   * as off-plan. */
  const matches = (field, actual, planned) => fills.has(field) || same(actual, planned);

  const loadOk = set.loadType === 'bodyweight'
    ? (fills.has('load') || v.load === 'BW')
    : matches('load', v.load, set.load);

  if (kind === 'weight_reps') return loadOk && matches('reps', v.reps, set.reps);
  if (kind === 'reps')        return matches('reps', v.reps, set.reps);
  if (kind === 'time')        return matches('duration', v.duration, set.duration);
  if (kind === 'carry')       return loadOk && matches('reps', v.reps, set.reps)
                                            && matches('distance', v.distance, set.distance);
  if (kind === 'cardio')      return matches('distance', v.distance, set.distance)
                                            && matches('duration', v.duration, set.duration);
  return true;
}
