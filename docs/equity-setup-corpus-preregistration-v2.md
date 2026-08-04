# Equity Setup Corpus v2 — Preregistration Correction

**BINDING.** Supersedes `docs/equity-setup-corpus-preregistration-v1.md`.

Status: **preregistration only.** No corpus batch has been authorized or run —
not under v1, and not under v2. **No data has ever been fetched under either
version.**

| Field | Value |
|---|---|
| Corpus version | `equity_corpus_v2` |
| Supersedes | `equity_corpus_v1` |
| v1 document SHA-256 | `69bcd953c0df02a3361131640479bc79ce44467c763f22962cc633abfc7eee26` |
| v1 status | **preserved, unmodified, superseded** |

---

## 0. Why this correction exists

v1 was frozen, then the offline batch-runner preflight was built against it. The
preflight surfaced three defects **in the frozen rules themselves** and one in
its own first implementation. All four were found **before any corpus fetch**,
so none of them is a result-dependent change — there are no results. That is the
entire reason a correction is legitimate here and would not be legitimate later.

v1 is not edited. It stays in the tree exactly as committed, and this document
records what it got wrong.

### D-1 — v1's RTH definition contaminates the primary dataset on half days

v1 §3 defines `RTH` as `09:30–16:00` with no calendar qualification. On an NYSE
early-close session, **core trading ends at 13:00** and the late session runs to
**17:00**. Under v1's flat rule, every bar between 13:00 and 16:00 on a half day
is labelled `RTH` and enters the primary validation set, when it is in fact
extended-hours activity — thinner, wider-spread, and mechanically different.

There are 5 such sessions in the frozen window (2024-11-29, 2024-12-24,
2025-07-03, 2025-11-28, 2025-12-24). Each contributes up to 36 misclassified
5-minute bars per symbol, so roughly 180 contaminated primary bars per symbol
across the window. This is small in proportion and completely avoidable, which
is exactly the kind of defect that is worth fixing before a fetch and impossible
to fix honestly after one.

### D-2 — v1 §13.3 says "missing-session count", which is not bar coverage

Counting sessions that have at least one bar proves almost nothing. A symbol
with a single aligned bar per session satisfies it. v1 has no rule requiring the
five-minute grid within a session to be complete, so "verified coverage" under
v1 could not detect an isolated missing bar, an hour-long hole, or dozens of
internal gaps.

### D-3 — one-page pagination was assumed, not proven

The first preflight implementation asserted that a calendar-quarter slice
(~12,100 output 5-minute bars) sits under the provider's 50,000-row ceiling and
therefore returns in a single page. **That is wrong.** The provider's `limit`
bounds the **base aggregates queried**, not the aggregated bars returned. A
quarter slice of 5-minute bars over an 04:00–20:00 session spans roughly
**60,480 one-minute base aggregates**, above the ceiling. One-page completion is
not provable and must never be encoded as an invariant.

### D-4 — checkpoints were not bound to the corpus bytes

The first implementation's checkpoints carried manifest, universe, code, window,
and config hashes, but nothing about the bar file itself. A resumed run returned
a synthetic PASS without reading the data. The file could be deleted, truncated,
or replaced after the checkpoint was written and the resumed preflight would
still pass. Provenance that does not bind the artefact is decoration.

---

## 1. Universe — UNCHANGED from v1

Membership is identical. The v1 universe file remains the pinned source; no v2
universe file is created, because duplicating 156 unchanged entries would create
two things that must be kept in sync and could silently diverge.

| Field | Value |
|---|---|
| Universe file | `research/universe/equity_corpus_v1_universe.json` |
| Universe version (inside the file) | `equity_corpus_v1` |
| Universe SHA-256 | `327c7dcd795acc9a11d0b14c6030f0a03e14960245e3ef8740f6bedde9b90a67` |
| Source file | `docs/options_watchlist_150.csv` |
| Source SHA-256 | `2770c80b9d6b745b481245b457957e45275ad3590d6aa5cdfc6f7ca4761f1d4d` |

156 entries, 155 setup candidates. Cohorts, pooling rules, the XLC exclusion,
and the VIX regime-input-only role are all carried over from v1 §1 unchanged.

**`equity_corpus_v2` is the corpus version.** It feeds request IDs and output
paths, so no v2 artefact can ever be confused with a v1 artefact.

## 2. Frozen window — UNCHANGED from v1

**2024-07-31 through 2026-07-30 inclusive, `America/New_York`,** via `zoneinfo`,
never a fixed UTC offset.

## 3. Session scope and tagging — CORRECTED (supersedes v1 §3)

Session bounds are **calendar-aware**. Every bar carries exactly one tag.

**Ordinary session:**

| Tag | Window (ET) |
|---|---|
| `PREMARKET` | 04:00 – 09:30 |
| `RTH` | 09:30 – 16:00 |
| `AFTER_HOURS` | 16:00 – 20:00 |

**Early-close session:**

| Tag | Window (ET) |
|---|---|
| `PREMARKET` | 04:00 – 09:30 |
| `RTH` | 09:30 – **13:00** |
| `AFTER_HOURS` | **13:00 – 17:00** |

Nothing outside those bounds is in the corpus. A bar on a non-trading date, or
outside the day's bounds, is a hard failure — never silently retagged.

**Frozen early-close calendar** (RTH 13:00, extended 17:00):
`2024-11-29`, `2024-12-24`, `2025-07-03`, `2025-11-28`, `2025-12-24`.

**Frozen full-closure calendar:** `2024-09-02`, `2024-11-28`, `2024-12-25`,
`2025-01-01`, `2025-01-09` (national day of mourning, President Carter),
`2025-01-20`, `2025-02-17`, `2025-04-18`, `2025-05-26`, `2025-06-19`,
`2025-07-04`, `2025-09-01`, `2025-11-27`, `2025-12-25`, `2026-01-01`,
`2026-01-19`, `2026-02-16`, `2026-04-03`, `2026-05-25`, `2026-06-19`,
`2026-07-03` (Independence Day observed; July 4 is a Saturday).

A real closure absent from this table surfaces as `EXTRA_SESSION`; a tabled date
that actually traded surfaces as `MISSING_SESSION`. Both fail closed. Neither is
silently absorbed.

Primary validation remains **RTH only**; extended-hours analysis remains
secondary; the two are never pooled into one figure.

## 4. Bar-coverage policy — NEW (supersedes v1 §13.3's "missing-session count")

Session presence is not coverage. Coverage is evaluated **per session, per tag,
against the expected five-minute grid**, under a policy frozen by asset class
**before** any fetch.

### 4.1 Equity and ETF cohorts — `single_name`, `etf`, `leveraged_inverse`

| Segment | Policy |
|---|---|
| `RTH` | **Complete grid required.** Every missing interval is enumerated. Any missing RTH interval is a **hard failure** unless the session appears in the frozen halt/exception table. |
| `PREMARKET` / `AFTER_HOURS` | **Enumerate and publish, do not fail.** Absent extended-hours intervals are expected: the provider emits no bar when no trade occurred, and thin names are legitimately sparse. Exact per-session expected / observed / missing / duplicate counts are recorded and enter the coverage hash. |

The strict RTH rule is deliberate. A pre-chosen tolerance would be an arbitrary
number invented before seeing a single bar; a hard failure forces every real gap
to be looked at and adjudicated on the record. **Adjudication happens by adding
a dated, reasoned entry to the frozen halt/exception table in a further versioned
correction — never by loosening the threshold after seeing results.**

**Frozen halt/exception table: empty at v2 freeze.**

### 4.2 Index cohort — VIX

**Enumerate and publish, do not fail**, in every segment. Index values are
published on update, not on trade, so an absent interval is not evidence of data
loss. VIX is excluded from all trade statistics regardless (v1 §1), so its
coverage never enters a performance figure.

### 4.3 Recorded for every symbol, every session

`expected`, `observed`, `missing`, `duplicate` — per session, per tag. These
enter the final report and are hashed into the per-symbol coverage hash, so a
coverage claim cannot be restated later without invalidating the checkpoint.

## 5. Pagination-completion evidence — NEW (fixes D-3)

**No one-page assumption exists anywhere in v2.** `1,404` remains the count of
**logical** requests (156 symbols × 9 calendar-quarter slices) and says nothing
about physical API calls. The batch runner is required to be pagination-safe and
to emit, per logical request:

1. every `next_url` followed until exhausted, with exhaustion explicitly recorded;
2. `page_count`;
3. the provider request ID of every page;
4. `queryCount` and `resultsCount` per page;
5. first and last bar timestamp per page and per logical request;
6. proof that no two pages duplicate or overlap;
7. a completion marker written **only after the final page**.

A symbol is complete only when evidence for **all nine** of its logical requests
is present and internally consistent with the bars on disk. Missing or partial
evidence is a hard failure. **Runtime duration remains an estimate until
measured**; no duration is preregistered.

## 6. Checkpoint binding — NEW (fixes D-4)

Every per-symbol checkpoint carries, and re-verifies on resume:

- bar-file **SHA-256**, **byte size**, and **row count**;
- **first and last timestamps**;
- the **per-session coverage hash** from §4.3;
- the **fetch-evidence hash** from §5;
- the five run hashes from v1's preflight: manifest, universe, code, window, config.

On resume the bar file and evidence file are **re-read and re-hashed**. Any
mismatch fails the run. A checkpoint may never stand in for data that is no
longer there or no longer the same.

## 7. Everything else — UNCHANGED from v1

Carried over verbatim, and still binding:

- §4 timeframe construction (one canonical 5-minute corpus; 15m/30m/1h/4h derived
  under frozen 09:30 ET anchors; no provider higher-timeframe aggregates; no
  derived bar crosses a session boundary; `is_partial_interval` preserved, never
  discarded, never treated as full-duration);
- §5 existing-data reuse (TQQQ/SQQQ refetched);
- §6 the 7 preregistered setups via the existing `strategy/strat_classifier.py`,
  no second detector;
- §7 mechanical trigger / invalidation / 1R–2R targets;
- §8 SPY-daily-only regime;
- §9 the two replay modes, reported separately;
- §10 same-bar ambiguity: stop-first primary, target-first sensitivity published,
  expectancy delta published;
- §11 evidence labels, Wilson intervals, bootstrap expectancy, calendar-midpoint
  walk-forward, full cell-count publication;
- §12 post-preregistration prohibitions.

Derived timeframes inherit the corrected calendar-aware session bounds from §3:
a derived bar still may not cross a session boundary, and on a half day that
boundary is 13:00, not 16:00.

## 8. Gate before the full batch — UNCHANGED and UNMET

The corpus batch is **NOT authorized** by this document. Required first:

1. This correction committed and SHA-pinned. ✅
2. One representative single-name smoke test. ❌ **not run**
3. Verification of earliest/latest timestamp, duplicate count, per-session bar
   coverage under §4, adjustment status, timezone/DST behaviour, derived-timeframe
   bar counts. ❌ **not run**
4. Restart/idempotency behaviour verified. ⚠️ **preflight implemented and tested
   offline; not exercised against real fetched data**
5. Pagination-completion evidence for a real multi-page request. ❌ **not run**
6. `I:VIX` endpoint/entitlement confirmation. ❌ **not run, not authorized**
7. **Explicit operator authorization** for the full batch. ❌ **not granted**

**Nothing deployed. No futures behaviour changed. No production code modified.
No corpus request of any kind has been made.**
