/** Mirrors the server's bounded temporary grant; never renews or extends it. */
export const WAYLO_MAX_GRANT_SECONDS = 15 * 60;
export const WAYLO_CAPTURE_RESERVE_SECONDS = 120;

export function wayloRunBudgetError(timeout: number, conversations: number, expiresAt?: string, now = Date.now()): string | null {
  const required = (timeout + WAYLO_CAPTURE_RESERVE_SECONDS) * conversations;
  if (required > WAYLO_MAX_GRANT_SECONDS) {
    return 'This run exceeds the 15-minute temporary Waylo access limit, including capture time. Choose a single scenario, fewer iterations, or a shorter session timeout. Larger suites require a saved server credential.';
  }
  if (expiresAt && (!Number.isFinite(Date.parse(expiresAt)) || required > (Date.parse(expiresAt) - now) / 1000)) {
    return 'Temporary Waylo access expires before this run can finish, including capture time. Reconnect or reduce the run size.';
  }
  return null;
}
