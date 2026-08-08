"""Reproduction for the premium_value defect. See docs/options-premium-value-defect-2026-08-08.md

Verifies repo Black-Scholes against Columbia IEOR E4706 (Haugh 2016) reference and
demonstrates that evaluate_option_value's "edge" is circular.
Run: python3 research/verify_premium_value_defect.py

READ-ONLY: imports repo code, mutates nothing.
Reference formulas (S1, eqs 13 + Greeks section):
  C = e^{-qT} S Phi(d1) - e^{-rT} K Phi(d2)
  d1 = [ln(S/K) + (r - q + sig^2/2)T] / (sig sqrt(T)); d2 = d1 - sig sqrt(T)
  gamma = e^{-qT} phi(d1) / (sig S sqrt(T))
  vega  = e^{-qT} S sqrt(T) phi(d1)
  put-call parity: e^{-rT}K + C = e^{-qT}S + P
"""
import os, sys, math
# repo root = parent of this file's directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alert_ranker.options_valuation import black_scholes_price, evaluate_option_value
from sources.gex_compute import bs_gamma

N = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
n = lambda x: math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def ref_bs(kind, S, K, T, r, q, sig):
    d1 = (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    if kind == "call":
        return math.exp(-q * T) * S * N(d1) - math.exp(-r * T) * K * N(d2)
    return math.exp(-r * T) * K * N(-d2) - math.exp(-q * T) * S * N(-d1)


def ref_gamma(S, K, T, r, q, sig):
    d1 = (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    return math.exp(-q * T) * n(d1) / (sig * S * math.sqrt(T))


print("=" * 70)
print("T1. repo black_scholes_price vs reference (q=0), max abs err")
print("=" * 70)
worst = 0.0
for S in (50, 100, 250, 600):
    for moneyness in (0.85, 0.95, 1.0, 1.05, 1.15):
        for dte in (1, 7, 30, 180):
            for iv in (0.15, 0.35, 0.80):
                K = S * moneyness
                r = 0.045
                T = dte / 365.0
                for kind in ("call", "put"):
                    got = black_scholes_price(
                        option_type=kind, underlying_price=S, strike=K,
                        dte=dte, implied_volatility=iv, risk_free_rate=r)
                    exp_ = max(ref_bs(kind, S, K, T, r, 0.0, iv), 0.0)
                    worst = max(worst, abs(got - exp_))
print(f"  max |repo - reference(q=0)| = {worst:.2e}   -> {'MATCH' if worst < 1e-3 else 'MISMATCH'}")

print()
print("=" * 70)
print("T2. put-call parity in repo implementation (q=0)")
print("=" * 70)
S, K, dte, iv, r = 100.0, 100.0, 30, 0.30, 0.045
T = dte / 365.0
c = black_scholes_price(option_type="call", underlying_price=S, strike=K, dte=dte,
                        implied_volatility=iv, risk_free_rate=r)
p = black_scholes_price(option_type="put", underlying_price=S, strike=K, dte=dte,
                        implied_volatility=iv, risk_free_rate=r)
lhs = math.exp(-r * T) * K + c
rhs = S + p
print(f"  e^-rT K + C = {lhs:.6f}   S + P = {rhs:.6f}   diff = {abs(lhs-rhs):.2e}")

print()
print("=" * 70)
print("T3. gex_compute.bs_gamma vs reference gamma (r=q=0)")
print("=" * 70)
worstg = 0.0
for S in (100, 450):
    for m in (0.9, 1.0, 1.1):
        for tte in (1/365, 7/365, 30/365):
            for iv in (0.2, 0.6):
                got = bs_gamma(S, S*m, tte, iv)
                exp_ = ref_gamma(S, S*m, tte, 0.0, 0.0, iv)
                worstg = max(worstg, abs(got - exp_))
print(f"  max |bs_gamma - reference(r=q=0)| = {worstg:.2e}  -> {'MATCH' if worstg < 1e-12 else 'MISMATCH'}")

print()
print("=" * 70)
print("T4. IS 'edge_percent' TAUTOLOGICAL? feed back the contract's own IV")
print("=" * 70)
print("  Columbia S1: 'sigma(K,T) is the volatility that, when substituted into")
print("  the Black-Scholes formula, gives the market price C(S,K,T)'.")
print("  So BS(own IV) == mark by construction -> edge must be ~0.\n")


def implied_vol(mark, kind, S, K, dte, r):
    lo, hi = 1e-4, 3.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        px = black_scholes_price(option_type=kind, underlying_price=S, strike=K,
                                 dte=dte, implied_volatility=mid, risk_free_rate=r)
        if px < mark:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


for (S, K, dte, mark, kind) in [(100, 100, 30, 3.50, "call"),
                                (450, 460, 7, 2.10, "call"),
                                (250, 240, 45, 6.80, "put")]:
    iv = implied_vol(mark, kind, S, K, dte, 0.045)
    res = evaluate_option_value({
        "underlying_price": S, "strike": K, "dte": dte,
        "implied_volatility": iv, "option_type": kind,
        "option_mark": mark, "risk_free_rate": 0.045,
    })
    print(f"  S={S} K={K} dte={dte} mark={mark} kind={kind}")
    print(f"    solved IV={iv:.6f}  theo={res.theoretical_value}  "
          f"edge={res.edge_percent}%  verdict={res.verdict} score={res.component_score}")

print()
print("=" * 70)
print("T5. what edge does a q!=0 (dividend) mismatch actually produce?")
print("=" * 70)
print("  Broker prices with true q; repo assumes q=0. SPY-like q=1.2%.")
for dte in (7, 30, 90, 365):
    S, K, r, q, iv = 450.0, 450.0, 0.045, 0.012, 0.18
    T = dte / 365.0
    true_mark = ref_bs("call", S, K, T, r, q, iv)
    repo_theo = black_scholes_price(option_type="call", underlying_price=S, strike=K,
                                    dte=dte, implied_volatility=iv, risk_free_rate=r)
    edge = (repo_theo - true_mark) / true_mark * 100.0
    print(f"    dte={dte:4d}  true(q={q})={true_mark:8.4f}  repo(q=0)={repo_theo:8.4f}  "
          f"edge={edge:+6.2f}%")

print()
print("=" * 70)
print("T6. IV unit heuristic: sigma = iv/100 if iv > 3 else iv")
print("=" * 70)
for iv_in, meaning in [(0.22, "22% as decimal"), (22.0, "22% as percent"),
                       (2.50, "250% as decimal"), (3.00, "300% as decimal"),
                       (3.50, "350% as decimal"), (350.0, "350% as percent")]:
    sigma = iv_in / 100.0 if iv_in > 3 else iv_in
    ok = "OK" if abs(sigma - (iv_in if iv_in <= 3 else iv_in / 100)) < 1e-12 else "?"
    print(f"    input={iv_in:7.2f} ({meaning:18s}) -> sigma={sigma:.4f} "
          f"({sigma*100:.1f}% vol)")
print("\n  Ambiguity: a genuine IV of 3.5 (=350% vol, real for 0DTE/earnings/meme)")
print("  is read as 3.5% vol. Boundary is iv>3, so decimal IVs above 300% break.")
