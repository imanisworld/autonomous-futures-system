# Proposed GROK.md amendment — review only

This is **not** an edit to the authoritative `GROK.md`; that file continues to prohibit Grok from independently restarting services or changing runtime configuration.

After an approved and verified installation of an exact, logged, fixed-argument maintenance interface, propose replacing the absolute prohibition with the following **narrow clarification**:

> Grok may inspect VPS status through an authorized read-only interface. Grok may carry out an **explicitly operator-approved**, fixed-argument maintenance action only through an independently reviewed, installed, logged restricted interface, with separate action authorization and verified pre/postconditions. A source-review PASS or agent instruction is never operational approval. Grok cannot create its own approval, grant itself privileges, run arbitrary shell or sudo, deploy unreviewed code, alter broker orders/risk state, or enable live trading. Restarts and demo/paper releases are **not** enabled by this policy change alone; each needs its own implemented, reviewed narrow interface and separate approval.

If Grok cannot reach the VPS from its cloud environment, it may review source and captured evidence but cannot claim to have executed server operations. Keep source review and actual execution independently accountable.

Do **not** amend `GROK.md` or change any agent permissions as part of this draft PR without a further explicit operator decision.
