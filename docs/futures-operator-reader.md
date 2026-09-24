# Futures Operator Reader

Read this first when picking up the futures system.

## Current source of truth

1. `docs/futures-current-state-handoff.md` — current source/runtime split plus long provenance. Read the newest dated block first.
2. `docs/futures-current-status-2026-09-22.md` — concise operator status. The filename is historical; newer dated sections inside the file supersede older sections.
3. `docs/strategy-rules/Strategy_Inventory.md` — strategy evidence classifications and execution posture.
4. Deployment state must still be verified on the box. Repository `main` is not proof of what the VPS is running.

## Runtime reading rule

- This reader intentionally does **not** pin a release SHA, PID, or deployment timestamp.
- Use the newest verified runtime block in the current-state/status documents, then verify the box before relying on it.
- Source may be ahead of the verified runtime. A merged PR is not deployment proof.
- No documentation entry grants live-trading authority.

## Research reading rule

Before treating a result as evidence, check:

1. the preregistration;
2. the research trial ledger identity/status;
3. the declared corpus/window and data identity;
4. the result artifact;
5. the strategy inventory/current-status disposition.

Exploratory or scratch work is not scored evidence merely because it produced a result.

## Current operator priorities

- Prefer existing evidence lanes and current preregistered studies over creating duplicate variants.
- Do not reopen retired or superseded work without a new preregistration and explicit reason.
- Keep research/evidence conclusions separate from runtime/deployment claims.
- Verify broker/account/session/release state independently before any operational action.

## Standing rule

No proof, no run.
