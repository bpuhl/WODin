/* What happens when the athlete finishes.
 *
 * Extracted so the decisions are testable. Both of them are product
 * choices rather than mechanics, and both were wrong for this deployment
 * before issue #22.
 */

/* Does this workout have somewhere to send a result?
 *
 * Every workout this deployment publishes does. A workout opened from a
 * shared #w= link carries no sink and never will — handing it back by
 * Share or Copy is the only route there is, which is exactly the case
 * upstream's four-button sheet was designed for. */
export function hasSink(wod) {
  const s = wod && wod.sink;
  return Boolean(s && s.type === 'post' && s.url);
}

/* Which actions the fallback sheet offers.
 *
 * Reaching the sheet means either there was nowhere to send the result, or
 * the send failed. So it never offers "Send to coach": re-offering the
 * thing that just failed as the primary action is a loop.
 *
 * It no longer offers "Download JSON" either. That existed so a result
 * could be recovered by hand; results now land in a bucket, which is a
 * better place to read them than a phone's downloads folder. */
export function sheetActions({ canShare }) {
  return canShare ? ['share', 'copy'] : ['copy'];
}
