/**
 * Schedule soft Signals refetches around each 15m bar close.
 *
 * Worker scans on bar close + settle (~8s). UI waits settle + lag, then bursts
 * a few short polls so Action/Trend pick up within ~15–30s of the close —
 * without a full page reload.
 */

export const BAR_TF_MINUTES = 15;
/** Matches worker ``bar_close_settle_sec`` default. */
export const BAR_CLOSE_SETTLE_MS = 8_000;
/** First soft refetch after settle (worker usually done by then). */
export const POST_CLOSE_LAG_MS = 15_000;
/** Extra short polls after the first post-close refetch. */
export const POST_CLOSE_BURST_MS = 15_000;
/** How long after close+settle to keep bursting (~settle+lag+2 bursts ≈ 53s). */
export const POST_CLOSE_BURST_WINDOW_MS = 45_000;
/** Slow keepalive when far from a boundary (e.g. mid-bar / off-hours). */
export const MID_BAR_KEEPALIVE_MS = 120_000;
export const MIN_WAIT_MS = 1_000;
export const MAX_WAIT_MS = 15 * 60_000;

/** UTC ms of the next 15m clock boundary (or ``now`` if exactly on one). */
export function nextBarCloseMs(
  nowMs: number = Date.now(),
  timeframeMinutes: number = BAR_TF_MINUTES,
): number {
  const tfMs = Math.max(1, timeframeMinutes) * 60_000;
  const rem = nowMs % tfMs;
  if (rem === 0) return nowMs;
  return nowMs + (tfMs - rem);
}

/** Most recent 15m boundary at or before ``nowMs``. */
export function latestBarCloseMs(
  nowMs: number = Date.now(),
  timeframeMinutes: number = BAR_TF_MINUTES,
): number {
  const tfMs = Math.max(1, timeframeMinutes) * 60_000;
  return nowMs - (nowMs % tfMs);
}

/**
 * Milliseconds to wait before the next soft Signals refetch.
 *
 * - Approaching / just after a close: align to close + settle + lag.
 * - Inside the post-close catch window: short burst polls.
 * - Mid-bar otherwise: long keepalive (still soft — no full reload).
 */
export function msUntilSignalsSoftRefresh(
  nowMs: number = Date.now(),
  opts?: {
    timeframeMinutes?: number;
    settleMs?: number;
    lagMs?: number;
    burstMs?: number;
    burstWindowMs?: number;
    keepaliveMs?: number;
  },
): number {
  const tf = opts?.timeframeMinutes ?? BAR_TF_MINUTES;
  const settle = opts?.settleMs ?? BAR_CLOSE_SETTLE_MS;
  const lag = opts?.lagMs ?? POST_CLOSE_LAG_MS;
  const burst = opts?.burstMs ?? POST_CLOSE_BURST_MS;
  const burstWindow = opts?.burstWindowMs ?? POST_CLOSE_BURST_WINDOW_MS;
  const keepalive = opts?.keepaliveMs ?? MID_BAR_KEEPALIVE_MS;

  const lastClose = latestBarCloseMs(nowMs, tf);
  const firstFetchAt = lastClose + settle + lag;
  const burstEnd = lastClose + settle + burstWindow;

  if (nowMs < firstFetchAt) {
    // Still waiting for this bar's first post-close fetch — unless that wait
    // is longer than a keepalive (e.g. just after previous window ended).
    const untilFirst = firstFetchAt - nowMs;
    if (untilFirst <= keepalive + settle + lag) {
      return clampWait(untilFirst);
    }
  } else if (nowMs < burstEnd) {
    return clampWait(burst);
  }

  const nextClose = nextBarCloseMs(nowMs + 1, tf); // strictly next if on boundary
  const nextFirst = nextClose + settle + lag;
  const untilNext = nextFirst - nowMs;
  if (untilNext > keepalive) {
    return clampWait(keepalive);
  }
  return clampWait(untilNext);
}

function clampWait(ms: number): number {
  if (!Number.isFinite(ms) || ms < MIN_WAIT_MS) return MIN_WAIT_MS;
  if (ms > MAX_WAIT_MS) return MAX_WAIT_MS;
  return Math.ceil(ms);
}
