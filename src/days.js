/* Stepping between the days that actually have a workout.
 *
 * "Previous day" must mean the previous day that HAS one, not yesterday.
 * Rest days leave gaps, and a control that lands on an empty date every
 * other tap is worse than no control at all.
 *
 * "Next" is only offered when there is one, which is why the caller needs
 * the whole list rather than probing a date at a time — probing cannot
 * know when to stop, and costs a request per tap.
 */

export function neighbours(dates, current) {
  const sorted = [...new Set(dates)].sort();
  const i = sorted.indexOf(current);

  if (i === -1) {
    // Viewing a date with no workout — offer the nearest either side, so a
    // signed-in athlete with nothing today can still reach the last one.
    const before = sorted.filter(d => d < current);
    const after = sorted.filter(d => d > current);
    return {
      prev: before.length ? before[before.length - 1] : null,
      next: after.length ? after[0] : null
    };
  }
  return {
    prev: i > 0 ? sorted[i - 1] : null,
    next: i < sorted.length - 1 ? sorted[i + 1] : null
  };
}

/* The most recent workout on or before a date — what to offer when today
 * has none. */
export function mostRecent(dates, onOrBefore) {
  const before = [...dates].sort().filter(d => d <= onOrBefore);
  return before.length ? before[before.length - 1] : null;
}
