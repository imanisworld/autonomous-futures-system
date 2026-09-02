"""Pure Phase-1 options contract shortlist.

This module does not fetch an option chain and does not place or prepare an
order. It consumes caller-supplied contract candidates and delegates the core
liquidity/DTE/Greeks/event checks to the existing
``evaluate_contract_constraints`` authority.

Selection-specific policy is explicit and has no trading defaults. In
particular, the caller must supply DTE, liquidity, premium, theta, and delta
limits. A preferred delta is optional: without one, multiple valid contracts
remain a shortlist and this module refuses to pretend it knows which contract
is best. This makes the ordering useful for a forward shadow campaign without
claiming an unproven contract-selection edge.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Literal, Optional
from .base import ContractConstraintsInputs
from .contract_validator import evaluate_contract_constraints
Direction = Literal["CALL", "PUT"]
RiskLevel = Literal["NONE", "LOW", "HIGH"]
SelectionStatus = Literal["INVALID_REQUEST","NO_ELIGIBLE","CAUTION_ONLY","SHORTLIST","SOLE_ELIGIBLE","PREFERRED_CANDIDATE"]

@dataclass(frozen=True, kw_only=True)
class ContractCandidate:
    symbol: str; ticker: str; direction: Direction; expiration: Optional[str]; dte: Optional[int]; strike: Optional[float]; premium: Optional[float]; bid: Optional[float]; ask: Optional[float]; volume: Optional[int]; open_interest: Optional[int]; delta: Optional[float]; theta: Optional[float]; iv: Optional[float]; earnings_risk: Optional[RiskLevel]; event_risk: Optional[RiskLevel]

def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)): return False
    try: parsed = float(value)
    except (TypeError, ValueError, OverflowError): return False
    return math.isfinite(parsed)

@dataclass(frozen=True, kw_only=True)
class ContractSelectionPolicy:
    max_premium_per_share: float; max_spread_percent: float; min_volume: int; min_open_interest: int; min_dte: int; max_theta_abs: float; min_abs_delta: float; max_abs_delta: float; preferred_abs_delta: Optional[float] = None
    def validation_errors(self) -> tuple[str, ...]:
        errors=[]
        for name,value in (("max_premium_per_share",self.max_premium_per_share),("max_spread_percent",self.max_spread_percent),("max_theta_abs",self.max_theta_abs)):
            if not _finite_number(value) or float(value)<=0: errors.append(f"{name} must be a finite number > 0")
        if isinstance(self.min_volume,bool) or not isinstance(self.min_volume,int) or self.min_volume<0: errors.append("min_volume must be an integer >= 0")
        if isinstance(self.min_open_interest,bool) or not isinstance(self.min_open_interest,int) or self.min_open_interest<0: errors.append("min_open_interest must be an integer >= 0")
        if isinstance(self.min_dte,bool) or not isinstance(self.min_dte,int) or self.min_dte<0: errors.append("min_dte must be an integer >= 0")
        delta_range_valid=(_finite_number(self.min_abs_delta) and _finite_number(self.max_abs_delta) and float(self.min_abs_delta)>0 and float(self.max_abs_delta)<=1 and float(self.min_abs_delta)<=float(self.max_abs_delta))
        if not delta_range_valid: errors.append("delta range must satisfy 0 < min_abs_delta <= max_abs_delta <= 1")
        if self.preferred_abs_delta is not None:
            valid=_finite_number(self.preferred_abs_delta) and delta_range_valid
            if valid:
                p=float(self.preferred_abs_delta); valid=float(self.min_abs_delta)<=p<=float(self.max_abs_delta)
            if not valid: errors.append("preferred_abs_delta must fall inside the configured delta range")
        return tuple(errors)

@dataclass(frozen=True, kw_only=True)
class ContractSelectionRequest: ticker: str; direction: Direction; candidates: tuple[ContractCandidate,...]; policy: ContractSelectionPolicy
@dataclass(frozen=True, kw_only=True)
class EvaluatedContractCandidate:
    candidate: ContractCandidate; validator_status: str; reason_code: str; reason: str; spread_percent: Optional[float]; warnings: tuple[str,...]=(); delta_distance: Optional[float]=None
    @property
    def valid_without_caution(self)->bool: return self.validator_status=="VALID"
@dataclass(frozen=True, kw_only=True)
class ContractShortlistResult:
    status: SelectionStatus; selected: Optional[EvaluatedContractCandidate]; eligible: tuple[EvaluatedContractCandidate,...]; rejected: tuple[EvaluatedContractCandidate,...]; blocking_reasons: tuple[str,...]=(); warnings: tuple[str,...]=()

def _spread_percent(bid: object, ask: object)->Optional[float]:
    try: b=float(bid); a=float(ask)
    except (TypeError,ValueError,OverflowError): return None
    if not math.isfinite(b) or not math.isfinite(a): return None
    midpoint=(b+a)/2.0
    if not math.isfinite(midpoint) or midpoint<=0: return None
    spread=(a-b)/midpoint*100.0
    return spread if math.isfinite(spread) else None

def _local_rejection(c,code,reason,*,spread_percent=None): return EvaluatedContractCandidate(candidate=c,validator_status="INVALID",reason_code=code,reason=reason,spread_percent=spread_percent)
def _malformed_numeric_reason(c):
    for name in ("dte","strike","premium","bid","ask","volume","open_interest","delta","theta","iv"):
        value=getattr(c,name)
        if value is not None and not _finite_number(value): return name
    return None

def _evaluate_candidate(c,*,ticker,direction,policy):
    nt=str(c.ticker or "").strip().upper()
    if not str(c.symbol or "").strip(): return _local_rejection(c,"missing_contract_symbol","contract symbol is required")
    if nt!=ticker: return _local_rejection(c,"ticker_mismatch",f"candidate ticker {nt!r} does not match request {ticker!r}")
    if c.direction!=direction: return _local_rejection(c,"direction_mismatch",f"candidate direction {c.direction!r} does not match request {direction!r}")
    if c.earnings_risk is None or c.event_risk is None: return _local_rejection(c,"event_risk_missing","earnings_risk and event_risk must both be explicitly resolved")
    if c.earnings_risk not in ("NONE","LOW","HIGH") or c.event_risk not in ("NONE","LOW","HIGH"): return _local_rejection(c,"event_risk_invalid","earnings_risk and event_risk must be NONE, LOW, or HIGH")
    malformed=_malformed_numeric_reason(c)
    if malformed: return _local_rejection(c,f"invalid_{malformed}",f"{malformed} must be finite numeric data when supplied")
    if c.strike is not None and c.strike<=0: return _local_rejection(c,"invalid_strike","strike must be > 0")
    if c.premium is not None and c.premium<=0: return _local_rejection(c,"invalid_premium","premium must be > 0")
    if c.iv is not None and c.iv<=0: return _local_rejection(c,"invalid_iv","iv must be > 0")
    if c.delta is None: return _local_rejection(c,"missing_delta","delta is required")
    ad=abs(float(c.delta))
    if ad<policy.min_abs_delta or ad>policy.max_abs_delta: return _local_rejection(c,"delta_out_of_range",f"abs(delta) {ad:g} outside configured range [{policy.min_abs_delta:g}, {policy.max_abs_delta:g}]")
    spread=_spread_percent(c.bid,c.ask)
    r=evaluate_contract_constraints(ContractConstraintsInputs(direction=direction,ticker=ticker,expiration=c.expiration,dte=c.dte,strike=c.strike,premium=c.premium,bid=c.bid,ask=c.ask,spread_percent=spread,volume=c.volume,open_interest=c.open_interest,delta=c.delta,theta=c.theta,iv=c.iv,max_premium=policy.max_premium_per_share,max_spread_percent=policy.max_spread_percent,min_volume=policy.min_volume,min_open_interest=policy.min_open_interest,min_dte=policy.min_dte,max_theta_abs=policy.max_theta_abs,earnings_risk=c.earnings_risk,event_risk=c.event_risk))
    dd=abs(ad-policy.preferred_abs_delta) if policy.preferred_abs_delta is not None else None
    return EvaluatedContractCandidate(candidate=c,validator_status=r.status,reason_code=r.reason_code,reason=r.reason,spread_percent=spread,warnings=tuple(r.warnings),delta_distance=dd)

def _ranking_key(e):
    c=e.candidate; return (e.delta_distance if e.delta_distance is not None else math.inf,e.spread_percent if e.spread_percent is not None else math.inf,-(c.open_interest if c.open_interest is not None else -1),-(c.volume if c.volume is not None else -1),c.strike if c.strike is not None else math.inf)

def shortlist_contracts(request):
    ticker=str(request.ticker or "").strip().upper(); errors=list(request.policy.validation_errors())
    if not ticker: errors.append("ticker is required")
    if request.direction not in ("CALL","PUT"): errors.append("direction must be CALL or PUT")
    if not request.candidates: errors.append("at least one contract candidate is required")
    if errors: return ContractShortlistResult(status="INVALID_REQUEST",selected=None,eligible=(),rejected=(),blocking_reasons=tuple(errors))
    evaluated=tuple(_evaluate_candidate(c,ticker=ticker,direction=request.direction,policy=request.policy) for c in request.candidates)
    eligible=tuple(x for x in evaluated if x.validator_status!="INVALID"); rejected=tuple(x for x in evaluated if x.validator_status=="INVALID"); clean=tuple(x for x in eligible if x.valid_without_caution); cautions=tuple(x for x in eligible if not x.valid_without_caution); warnings=tuple(dict.fromkeys(w for x in cautions for w in x.warnings))
    if not eligible: return ContractShortlistResult(status="NO_ELIGIBLE",selected=None,eligible=(),rejected=rejected,blocking_reasons=("no contract passed the explicit selection policy",))
    if not clean: return ContractShortlistResult(status="CAUTION_ONLY",selected=None,eligible=eligible,rejected=rejected,warnings=warnings)
    ranked=tuple(sorted(clean,key=_ranking_key)); all_eligible=(*ranked,*cautions)
    if len(ranked)==1: return ContractShortlistResult(status="SOLE_ELIGIBLE",selected=ranked[0],eligible=all_eligible,rejected=rejected,warnings=warnings)
    if request.policy.preferred_abs_delta is None: return ContractShortlistResult(status="SHORTLIST",selected=None,eligible=all_eligible,rejected=rejected,warnings=warnings)
    return ContractShortlistResult(status="PREFERRED_CANDIDATE",selected=ranked[0],eligible=all_eligible,rejected=rejected,warnings=warnings)
