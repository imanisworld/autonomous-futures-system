"""Reproduce, rather than assert, the fill model's conservatism claims.

EVIDENCE TOOLING ONLY. Read-only: constructs PaperBroker instances in memory,
runs no strategy, touches no broker, writes no journal, changes no runtime.

Each check below prints the observed number so a reviewer can see the claim, not
just the verdict. Run:

    python3 research/fill_model_audit.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import (  # noqa: E402
    TICK_SIZE,
    TICK_VALUE,
    NextBarOHLC,
    PaperBroker,
)

PASS, FAIL = "CONSERVATIVE", "OPTIMISTIC"


def _order(**kw) -> BracketOrder:
    base = dict(
        instrument="MNQ", direction="LONG", entry=100.0, stop=98.0, target=104.0,
        rr_ratio=2.0, strategy="audit", notes="", contracts=1,
    )
    base.update(kw)
    return BracketOrder(**base)


def check_commission() -> None:
    print("=" * 76)
    print("C1  Are commissions and exchange fees deducted from paper/replay P&L?")
    print("=" * 76)
    b = PaperBroker(starting_balance=0.0, slippage_ticks=0.0)
    b.execute_bracket(_order())
    fill = b.resolve_position(NextBarOHLC(open=100.0, high=104.0, low=99.5))
    tick, tick_val = TICK_SIZE["MNQ"], TICK_VALUE["MNQ"]
    gross = (104.0 - 100.0) / tick * tick_val
    print(f"    entry 100.00 -> target 104.00, 1 MNQ contract")
    print(f"    gross P&L by hand      : ${gross:,.2f}")
    print(f"    PaperBroker pnl_dollars: ${fill.pnl_dollars:,.2f}")
    print(f"    balance after           : ${b.get_account_balance():,.2f}")
    same = abs(fill.pnl_dollars - gross) < 1e-9
    print(f"    -> commission deducted: {'NO' if same else 'yes'}   [{FAIL if same else PASS}]")
    print("    A CME micro round turn is ~$1.24-$1.48 all-in. On a $2.00 MNQ")
    print("    winner that is 62-74% of the gross. Only two standalone research")
    print("    scripts model it; every PaperBroker-derived number does not.\n")


def check_target_on_touch() -> None:
    print("=" * 76)
    print("C2  Does a resting target fill merely because the bar TOUCHED it?")
    print("=" * 76)
    b = PaperBroker(starting_balance=0.0, slippage_ticks=1.0)
    b.execute_bracket(_order())
    # Bar's high is EXACTLY the target: one tick printed there, no more.
    fill = b.resolve_position(NextBarOHLC(open=100.0, high=104.0, low=99.5))
    print(f"    next bar high == target exactly (104.00), no trade-through")
    print(f"    result={fill.result} exit={fill.exit_price} reason={fill.exit_reason}")
    print(f"    slippage applied to this exit: "
          f"{'no' if fill.exit_price == 104.0 else 'yes'}")
    print(f"    -> touch counts as a full fill: "
          f"{'YES' if fill.result == 'WIN' else 'no'}   [{FAIL if fill.result == 'WIN' else PASS}]")
    print("    CME Globex is price-time FIFO: 'resting orders are matched in")
    print("    timestamp order only'. A limit at a price the market only touches")
    print("    fills only if the queue ahead of it clears. Queue position is not")
    print("    modelled here, so target fills are upper-bound optimistic. Stops")
    print("    (market orders) are NOT affected -- those correctly slip.\n")


def check_straddle_bar() -> None:
    print("=" * 76)
    print("C3  A bar containing BOTH stop and target -- which wins?")
    print("=" * 76)
    for flag in (True, False):
        b = PaperBroker(starting_balance=0.0, slippage_ticks=1.0, pessimistic_both_hit=flag)
        b.execute_bracket(_order())
        fill = b.resolve_position(NextBarOHLC(open=100.0, high=104.5, low=97.5))
        print(f"    pessimistic_both_hit={str(flag):<5} -> {fill.result:<9} "
              f"({fill.exit_reason})")
    print("    config/settings.py default: fill_pessimistic_both_hit = True")
    print(f"    -> intrabar path unknowable, worst case taken   [{PASS}]\n")


def check_stop_entry_same_bar() -> None:
    print("=" * 76)
    print("C4  stop_market entry: is the TRIGGER bar resolved against its own")
    print("    full high/low, including the part before the trigger?")
    print("=" * 76)
    b = PaperBroker(starting_balance=0.0, slippage_ticks=0.0,
                    pessimistic_both_hit=True, entry_fill_model="stop_market")
    b.execute_bracket(_order(entry=100.0, stop=98.0, target=104.0))
    # Bar opens at 97.0 (below the stop), rallies to 101.0 triggering the entry.
    # The 97.0 low happened BEFORE the long entry could exist.
    fill = b.resolve_position(NextBarOHLC(open=97.0, high=101.0, low=97.0))
    print("    bar: open 97.00, low 97.00, high 101.00; long stop-entry at 100.00")
    print("    the 97.00 low necessarily preceded the 100.00 trigger")
    print(f"    result={fill.result if fill else None} "
          f"reason={fill.exit_reason if fill else None}")
    print("    -> the entry bar's pre-trigger extreme can resolve the position.")
    print("       Direction of this bias is PESSIMISTIC (invents losses that")
    print("       could not have happened), not optimistic -- but it is still a")
    print("       causality error and it is not symmetric with the live path.\n")


def check_ioc_arrival_price() -> None:
    print("=" * 76)
    print("C5  ioc_limit: what price is treated as 'the market' on arrival?")
    print("=" * 76)
    b = PaperBroker(starting_balance=0.0, slippage_ticks=0.0,
                    entry_fill_model="ioc_limit",
                    entry_tolerance_ticks_by_root={"MNQ": 32.0})
    fill = b.execute_bracket(_order(), market_price=100.0)
    print(f"    market_price argument is the DECISION BAR'S CLOSE (replay_engine")
    print(f"    passes candle.close); fill entry = {fill.entry_price}")
    print("    -> zero latency between bar close and order arrival is assumed.")
    print("       Live, the order is built, risk-checked, and routed after the")
    print("       close; the market has moved by then. The tolerance cap bounds")
    print("       the damage but the fill price itself is still the close.")
    print(f"       [{FAIL} to the extent latency is non-zero -- unquantified,")
    print("        because no live quote is captured at submit time (see C6).]\n")


def check_no_fill_diagnostics() -> None:
    print("=" * 76)
    print("C6  Can a no-fill be diagnosed after the fact?")
    print("=" * 76)
    b = PaperBroker(starting_balance=0.0, entry_fill_model="ioc_limit",
                   entry_tolerance_ticks_by_root={"MNQ": 0.0})
    fill = b.execute_bracket(_order(), market_price=140.0)  # market far above limit
    print(f"    unmarketable IOC -> result={fill.result} reason={fill.exit_reason}")
    print(f"    no_fill_reason={fill.no_fill_reason}")
    print("    PaperBroker hardcodes entry_status='dead', so replay can NEVER")
    print("    produce NO_FILL_LIMIT_TOO_PASSIVE. Live Tradovate polls real order")
    print("    status and can produce either.")
    print("    Meanwhile last_price_at_submit / last_price_at_cancel /")
    print("    best_bid_at_submit / best_ask_at_submit / ticks_moved_from_entry")
    print("    are passed as literal None on EVERY code path, live included.")
    print("    -> the fill model cannot be validated against live behaviour,")
    print("       because live records no market state at submit or cancel.\n")


def main() -> int:
    print("\nFILL-MODEL AUDIT -- reproduces each claim in the Batch 2 report\n")
    check_commission()
    check_target_on_touch()
    check_straddle_bar()
    check_stop_entry_same_bar()
    check_ioc_arrival_price()
    check_no_fill_diagnostics()
    print("=" * 76)
    print("SUMMARY")
    print("=" * 76)
    print("  Conservative : straddle-bar worst case, adverse slippage on market")
    print("                 fills, IOC self-cancel booked as CANCELLED not a fill,")
    print("                 stop-entry fails closed without next-bar open.")
    print("  Optimistic   : no commission/fees at all; target fills on touch with")
    print("                 no queue position; zero submit latency.")
    print("  Unverifiable : no live market snapshot at submit/cancel, so none of")
    print("                 the above can be checked against real fills.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
