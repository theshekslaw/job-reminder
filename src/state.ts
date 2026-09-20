/**
 * Application state machine (DESIGN.md). Every change goes through transition(),
 * which validates the edge and appends to the transitions audit trail atomically.
 */

import { sql } from "./db";

export const STATES = [
  "DISCOVERED", "EVALUATED", "DRAFTED", "COVERAGE_CHECK", "COVERAGE_BLOCKED",
  "VALIDATED", "AWAITING_APPROVAL", "REJECTED", "PUBLISHED",
] as const;
export type State = (typeof STATES)[number];

const ALLOWED: Record<State, State[]> = {
  DISCOVERED:        ["EVALUATED", "REJECTED"],
  EVALUATED:         ["DRAFTED", "REJECTED"],
  DRAFTED:           ["COVERAGE_CHECK"],
  COVERAGE_CHECK:    ["VALIDATED", "DRAFTED", "COVERAGE_BLOCKED"],
  COVERAGE_BLOCKED:  ["VALIDATED", "REJECTED"], // VALIDATED only via HUMAN_OVERRIDE
  VALIDATED:         ["AWAITING_APPROVAL"],
  AWAITING_APPROVAL: ["DRAFTED", "REJECTED", "PUBLISHED"], // revise / reject / approve
  REJECTED:          [],
  PUBLISHED:         [],
};

export class IllegalTransition extends Error {}

export async function transition(
  applicationId: string,
  to: State,
  opts: { reason?: string; actor?: string } = {},
): Promise<void> {
  const actor = opts.actor ?? "system";
  await sql.begin(async (tx) => {
    const rows = await tx`
      SELECT state FROM applications
      WHERE application_id = ${applicationId} FOR UPDATE`;
    if (rows.length === 0) throw new IllegalTransition(`unknown application ${applicationId}`);
    const from = rows[0].state as State;
    if (!ALLOWED[from]?.includes(to)) {
      throw new IllegalTransition(`illegal transition ${from} → ${to} for ${applicationId}`);
    }
    // COVERAGE_BLOCKED → VALIDATED is the recorded human override, never implicit.
    if (from === "COVERAGE_BLOCKED" && to === "VALIDATED") {
      if (!opts.reason || actor === "system") {
        throw new IllegalTransition(
          "COVERAGE_BLOCKED → VALIDATED requires HUMAN_OVERRIDE: pass an explicit reason and a human actor",
        );
      }
      await tx`
        UPDATE applications SET
          override_reason = ${opts.reason},
          override_actor = ${actor},
          override_at = now(),
          original_coverage_score = keyword_coverage_score
        WHERE application_id = ${applicationId}`;
    }
    await tx`
      UPDATE applications SET state = ${to}, updated_at = now()
      WHERE application_id = ${applicationId}`;
    await tx`
      INSERT INTO transitions (application_id, from_state, to_state, reason, actor)
      VALUES (${applicationId}, ${from}, ${to}, ${opts.reason ?? null}, ${actor})`;
  });
}
