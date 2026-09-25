# 4HR Re-Trigger stop anchor on an exact-hour close — audit 2026-09-24

**Verdict: NOT A RUNTIME LOOKAHEAD. No strategy code was changed.**

Base: `34177183d2a2abbb2442b4bd2dee3f6579875140` (`main`).

This note checks one claim: `_completed_one_hour_stop` in
`strategy/four_hr_retrigger.py`, called with the trigger bar's close, can
select the 1-hour bar that contains that trigger bar when the 5-minute bar
closes exactly on the hour. That selection is real. It is the last hour that
has closed at the state machine's own entry time. It is lookahead only if
that same stop is reused for a fill assumed to happen inside the bar, before
the hour ends. The pre-armed harness already does not do that. The shared
live and replay function was left as it is.

This does not validate the 4HR strategy. It does not authorize a deploy.
The armed MNQ wide-stop demo lane reads this same close-time function and
does not need a separate code change for this question.

## The rule the function is implementing

`docs/strategy-rules/4HR_ReTrigger_Rules.md` §5:

- Stop = low of the last completed 1-hour candle at entry time (calls), or
  the high (puts).
- "Last completed" means the most recently closed 1-hour candle at the
  moment of entry. The candle that is still open does not count.
- Examples locked by `tests/test_four_hr_retrigger_executable.py`: entry at
  9:35 or 9:55 uses the 8:00 candle; entry at 10:05 or 10:35 uses the 9:00
  candle.

`advance_4hr_retrigger` defines that moment. `current_bar_ts` is the
5-minute bar's open. Entry time is that open plus 5 minutes, the bar-arrival
clock shared by live (`strategy/signal_engine.py`) and replay
(`replay/replay_engine.py`). Both call this function. Neither has a second
stop-anchor implementation.

The helper is:

```python
current_hour = et_bucket_start(entry_time, 60)
required_start = current_hour - timedelta(hours=1)
```

`et_bucket_start` of an exact hour returns that hour. Subtracting one hour
returns the hour that just ended. A 5-minute bar is admitted to the aggregate
when `bar_open + 5 minutes <= current_close`. The same `<=` test is how this
file decides a bar has closed.

## Reproduction

Constructed Tuesday 2026-01-06 bars. The 8:00 hour's low is 87. The 9:00
hour's other bars stay at or above 93. The trigger bar's low is 89.25, so a
stop of 89.25 can come only from the hour that includes the trigger bar.
LONG trigger is the 4:00 high, 95. The call is the real
`advance_4hr_retrigger`.

| Trigger open | Entry (close) | Stop | Stop bar | Bar end ≤ entry? | Stop bar contains the trigger bar? |
|---|---|---|---|---|---|
| 09:30 | 09:35 | 87 | 08:00–09:00 | yes | no |
| 09:50 | 09:55 | 87 | 08:00–09:00 | yes | no |
| 09:55 | 10:00 | 89.25 | 09:00–10:00 | yes | yes |
| 10:00 | 10:05 | 93 | 09:00–10:00 | yes | no |
| 10:30 | 10:35 | 93 | 09:00–10:00 | yes | no |
| 10:55 | 11:00 | 89.25 | 10:00–11:00 | yes | yes |

The 09:55 and 10:55 rows are the exact-hour case. The selected hour contains
the trigger bar, and 89.25 is that bar's own low. The hour's end is equal to
the entry close, so `end <= entry` is true. One second before the close,
`end <= as-of` is false. At the bar open, the same helper returns the prior
hour (87 at 08:00 for the 09:55 bar; 93 at 09:00 for the 10:55 bar).

Passing the bar open instead of the close changes the stop only on those two
rows. The four neighboring rows are identical. That is why an on-the-hour
sample can move while every other close time stays put.

## Why the close-time path is causal

At 10:00 the 09:00–10:00 candle has closed. The 10:00–11:00 candle is the
one that is open. The written rule selects the candle that just closed. The
trigger bar is inside that hour, and its high and low are known at the same
instant the 5-minute bar arrives. Live and the wide-stop collector decide at
that arrival. The collector then refuses to resolve the stop or target
against the signal bar; the next 5-minute bar starts at `entry_time`
(`context/wide_stop_forward_collector.py`).

Using the just-closed hour is the same rule the existing tests already lock
one bar later: a 10:05 entry uses the 09:00 candle because that candle closed
at 10:00. A 10:00 entry is the first moment that candle is closed, not a
later one.

## Why an intra-bar fill would be a different question

If the fill is assumed at a touch inside the 09:55–10:00 bar, the 09:00 hour
is still open. Its final extreme can be the trigger bar's own extreme, which
may print after the touch. Using 89.25 as the stop for that earlier fill is
lookahead.

That is the model already measured on 2026-09-18
(`docs/4hr-prearmed-touch-ab-2026-09-18.md`). The artifact
`scripts/4hr_prearmed_touch_ab_2026-09-18.json` records two MNQ dates whose
5-minute trigger closed at 10:00 ET:

| Day | Direction | Close-time stop | Stop from the bar open | Open-time stop bar |
|---|---|---|---|---|
| 2025-01-14 | SHORT | 21129.25 | 21139.00 | 08:00 |
| 2026-03-10 | SHORT | 25081.00 | 24999.50 | 08:00 |

The pre-armed portfolio path does not use the close-time stop for those
fills. `scripts/mnq_combined_portfolio_audit.py`
(`_prepare_4hr_causal_candidates`) calls `_completed_one_hour_stop` with the
trigger bar's **open**. On a 09:55 open that selects the 08:00 hour, which
ended at 09:00, before the bar started. The 1-minute evidence lane is a
separate helper, `_fully_completed_one_hour_stop` in
`context/one_min_trigger.py`, and it keeps an hour only when
`hour_start + 1 hour <= touch_bar_open`.

The 2026-09-18 note already says this difference does not authorize a runtime
change. Replacing the close-time argument with the bar open would do that
change: live, replay, and the wide-stop lane would drop a completed hour on
every exact-hour close, including a decision made at the close and a
next-open fill after it. That is a different entry clock, not a repair of
the clock the function has.

The private handoff log (`imanisworld/afs-handoff`, around 2026-09-24 11:20Z)
was not readable from this environment. The two dates and the mechanism match
the committed 2026-09-18 artifact. This audit did not re-scan a bar corpus,
so it does not claim those are the only historical dates. The same shape
exists for a 10:55–11:00 trigger, which is still inside the 09:30–11:00 entry
window (`current_open >= 11:00` expires). Whether any historical candidate
uses that bar was not rechecked here.

## What was not run

`data/replay_polygon_5m` is not in this checkout. The 4HR stop study
(`scripts/four_hr_retrigger_stop_study.py`) and the pre-armed portfolio
replay were **NOT RUN**. No before/after trade count, profit factor, win
rate, average R, or drawdown is reported, and none was estimated.

`pytest` was not run. This image did not install project dependencies
(`python-dotenv` and `pydantic` are absent; the environment install script
skipped). The reproduction imported `strategy.four_hr_retrigger` directly.

## What would have been a code change, and why it was not made

The single variable under test was stop-anchor bar selection. The before
rule, which is still the code, is: at the bar close, take the 1-hour candle
whose start is one hour before the hour bucket that contains that close. On
an exact hour, that candle is the one that ends at the close. The after rule
that was considered and rejected: take the last 1-hour candle whose end is
`<=` the trigger bar's open. That matches the pre-armed caller. It disagrees
with the close-time rule on the exact-hour rows above and nowhere else in
the table. No other parameter was touched. `risk_rules.yaml`, Pine, broker
adapters, deployment, and every other strategy were not edited.
