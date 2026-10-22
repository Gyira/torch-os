// First-run guidance is only for a genuinely empty day: no journal, tasks or habits.
// Kept DOM-free so the rule can be unit-tested.

export function shouldShowWelcome(payload, dismissed = false) {
  if (dismissed || !payload) return false;
  // The workbench intentionally creates today's diary template during its daily
  // sync, so its existence is not a "user has already started" signal.
  return !(payload.todos?.length)
    && !(payload.daily?.length)
    && !(payload.habits?.length);
}
