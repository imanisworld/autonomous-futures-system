# CLAUDE.md

Follow `AGENTS.md` as the repository-wide operating contract.

## Claude-specific guidance

- Use the existing commands in `.claude/commands/` instead of inventing duplicate audit procedures.
- For general futures state use `/futures-full-audit`; for a change review use `/futures-diff-review`; for deployment verification use `/futures-deployment-safety-audit`.
- Use the corresponding options commands for options work.
- These audits are evidence gates, not permission to deploy. Deployment remains a separate explicit operator-directed action.

## VPS / deployment

- The Claude VPS identity is `claude-audit`; do not substitute root or another agent's identity.
- Do not edit the live symlink target or immutable release directories in place.
- Work from a Git checkout/worktree and deploy only an exact reviewed SHA through the sanctioned controlled-release path.
- Never read or print production secrets merely because filesystem or sudo access makes them reachable.
- If Claude is granted a controlled deploy wrapper, use only that wrapper for privileged deployment actions; do not seek broader sudo access.
- Respect the deploy lock and all release/posture/integrity gates. Never force a lock without explicit operator authorization and proof that the existing lock is stale.

## Session closeout

Before reporting completion, verify the result from source and, when applicable, from the VPS runtime. State unverified items explicitly.
