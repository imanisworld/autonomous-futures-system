"""Unit tests for the pure GEX math (sources/gex_compute.py)."""

import pytest

from sources.gex_compute import (
    CONTRACT_MULTIPLIER,
    GexLeg,
    bs_gamma,
    compute_gex,
    dollar_gamma,
    infer_spot_from_parity,
    zero_gamma_level,
)


def test_dollar_gamma_sign_and_magnitude():
    spot = 700.0
    call = GexLeg(strike=700, is_call=True, gamma=0.05, open_interest=1000)
    put = GexLeg(strike=700, is_call=False, gamma=0.05, open_interest=1000)
    expected_mag = 0.05 * 1000 * CONTRACT_MULTIPLIER * spot * spot * 0.01
    assert dollar_gamma(call, spot) == expected_mag      # calls positive
    assert dollar_gamma(put, spot) == -expected_mag      # puts negative


def test_net_gex_positive_when_calls_dominate():
    legs = [
        GexLeg(710, True, 0.04, 5000),    # big call gamma
        GexLeg(690, False, 0.04, 1000),   # small put gamma
    ]
    prof = compute_gex(legs, spot=700.0)
    assert prof.ok
    assert prof.net_gex > 0
    assert prof.regime == "positive"


def test_net_gex_negative_when_puts_dominate():
    legs = [
        GexLeg(710, True, 0.04, 1000),
        GexLeg(690, False, 0.04, 5000),   # big put gamma
    ]
    prof = compute_gex(legs, spot=700.0)
    assert prof.ok
    assert prof.net_gex < 0
    assert prof.regime == "negative"


def test_call_and_put_walls_pick_extreme_strikes():
    legs = [
        GexLeg(720, True, 0.03, 8000),    # dominant call wall
        GexLeg(705, True, 0.01, 500),
        GexLeg(680, False, 0.03, 9000),   # dominant put wall
    ]
    prof = compute_gex(legs, spot=700.0)
    assert prof.call_wall == 720
    assert prof.put_wall == 680


def test_flip_point_interpolates_between_strikes():
    # Per-strike net $-gamma: 690→-40M, 700→+10M, 710→+50M.
    # Cumulative: 690=-40M, 700=-30M, 710=+20M → crosses zero between 700 and 710.
    # frac = 30/(30+20) = 0.6 → flip = 700 + 0.6*10 = 706.
    legs = [
        GexLeg(690, False, 0.05, 1632.6531),   # ≈ -40M at spot 700
        GexLeg(700, True, 0.05, 408.1633),     # ≈ +10M
        GexLeg(710, True, 0.05, 2040.8163),    # ≈ +50M
    ]
    prof = compute_gex(legs, spot=700.0)
    assert prof.flip_point is not None
    assert abs(prof.flip_point - 706.0) < 0.5
    # flip always lands within the traded strike range
    assert 690 <= prof.flip_point <= 710


def test_flip_point_none_when_no_sign_change():
    legs = [
        GexLeg(700, True, 0.05, 2000),
        GexLeg(710, True, 0.05, 2000),
    ]
    prof = compute_gex(legs, spot=700.0)
    # all-positive cumulative never crosses zero
    assert prof.flip_point is None


def test_parity_recovers_spot():
    # S = 700: at K=700, C - P should be ~0; at K=695, C - P ~ +5; K=705 ~ -5.
    pairs = [
        (700.0, 5.0, 5.0),    # C - P = 0 → S = 700
        (695.0, 8.0, 3.0),    # C - P = 5 → S = 700
        (705.0, 3.0, 8.0),    # C - P = -5 → S = 700
    ]
    spot = infer_spot_from_parity(pairs)
    assert spot is not None
    assert abs(spot - 700.0) < 1e-6


def test_parity_none_without_pairs():
    assert infer_spot_from_parity([]) is None
    assert infer_spot_from_parity([(700.0, None, 5.0)]) is None


def test_compute_failsoft_on_empty_and_no_spot():
    assert compute_gex([], spot=700.0).ok is False
    assert compute_gex([], spot=700.0).error == "no_legs"
    legs = [GexLeg(700, True, 0.05, 1000)]
    assert compute_gex(legs, spot=None).ok is False
    assert compute_gex(legs, spot=0).error == "no_spot"


def test_bs_gamma_peaks_near_atm_and_guards():
    # Gamma is largest at-the-money and decays away from it.
    atm = bs_gamma(700, 700, 7 / 365, 0.20)
    otm = bs_gamma(700, 760, 7 / 365, 0.20)
    assert atm > otm > 0
    # degenerate inputs → 0, never a crash or NaN
    assert bs_gamma(700, 700, 0, 0.20) == 0.0
    assert bs_gamma(700, 700, 7 / 365, 0) == 0.0
    assert bs_gamma(0, 700, 7 / 365, 0.20) == 0.0


def test_zero_gamma_recovers_symmetric_flip():
    # Symmetric book around 700: puts below, calls above, equal OI/IV/TTE.
    # Total dealer gamma crosses zero ≈ 700.
    iv, tte = 0.20, 7 / 365
    legs = [
        GexLeg(690, False, 0.0, 4000, iv=iv, tte_years=tte),
        GexLeg(695, False, 0.0, 4000, iv=iv, tte_years=tte),
        GexLeg(705, True, 0.0, 4000, iv=iv, tte_years=tte),
        GexLeg(710, True, 0.0, 4000, iv=iv, tte_years=tte),
    ]
    flip = zero_gamma_level(legs, spot=700.0)
    assert flip is not None
    assert abs(flip - 700.0) < 3.0


def test_zero_gamma_none_without_iv_or_tte():
    legs = [GexLeg(700, True, 0.05, 4000), GexLeg(710, False, 0.05, 4000)]  # no iv/tte
    assert zero_gamma_level(legs, spot=700.0) is None


def test_zero_gamma_drops_insane_iv_legs():
    # All legs carry the expiry-day 500%+ IV blowup → none usable → None.
    legs = [GexLeg(690 + 5 * i, i % 2 == 0, 0.0, 4000, iv=5.7, tte_years=1 / 365) for i in range(6)]
    assert zero_gamma_level(legs, spot=700.0) is None


def test_compute_gex_prefers_bs_flip_when_iv_present():
    iv, tte = 0.20, 7 / 365
    legs = [
        GexLeg(690, False, 0.04, 4000, iv=iv, tte_years=tte),
        GexLeg(695, False, 0.04, 4000, iv=iv, tte_years=tte),
        GexLeg(705, True, 0.04, 4000, iv=iv, tte_years=tte),
        GexLeg(710, True, 0.04, 4000, iv=iv, tte_years=tte),
    ]
    prof = compute_gex(legs, spot=700.0)
    assert prof.flip_point is not None
    assert abs(prof.flip_point - 700.0) < 3.0  # BS solve, near spot


def test_to_dict_is_compact_and_rounded():
    legs = [GexLeg(690, False, 0.05, 2000), GexLeg(710, True, 0.05, 2000)]
    d = compute_gex(legs, spot=700.0).to_dict()
    assert set(d) == {
        "ok", "spot", "net_gex", "flip_point", "dist_to_flip", "spot_vs_flip",
        "call_wall", "put_wall", "call_walls", "put_walls", "regime",
        "net_dex", "delta_bias", "n_legs", "error",
    }
    assert "per_strike" not in d  # heavy field excluded from journal record
    assert d["ok"] is True


def test_net_dex_sign_and_bias():
    spot = 700.0
    # heavier long-call delta than short-put delta → net long → bullish
    bull = [
        GexLeg(700, True, 0.04, 5000, delta=0.55),
        GexLeg(690, False, 0.04, 1000, delta=-0.30),
    ]
    prof = compute_gex(bull, spot)
    assert prof.net_dex > 0
    assert prof.delta_bias == "bullish"
    # put delta dominates → net short → bearish
    bear = [
        GexLeg(700, True, 0.04, 1000, delta=0.45),
        GexLeg(690, False, 0.04, 6000, delta=-0.55),
    ]
    assert compute_gex(bear, spot).delta_bias == "bearish"


def test_net_dex_none_without_delta():
    legs = [GexLeg(700, True, 0.05, 2000), GexLeg(710, False, 0.05, 2000)]  # no delta
    prof = compute_gex(legs, spot=700.0)
    assert prof.net_dex is None
    assert prof.delta_bias is None


def test_multi_walls_top_n_ordered():
    legs = [
        GexLeg(720, True, 0.05, 9000),   # strongest call wall
        GexLeg(715, True, 0.05, 5000),
        GexLeg(725, True, 0.05, 1000),
        GexLeg(680, False, 0.05, 9000),  # strongest put wall
        GexLeg(685, False, 0.05, 4000),
    ]
    prof = compute_gex(legs, spot=700.0)
    assert prof.call_walls[0] == 720 and prof.call_wall == 720
    assert prof.call_walls == [720, 715, 725]      # strongest-first, capped at 3
    assert prof.put_walls[0] == 680 and prof.put_wall == 680
    assert prof.put_walls == [680, 685]


def test_dist_to_flip_and_spot_side():
    legs = [
        GexLeg(690, False, 0.04, 1632.6531, iv=0.2, tte_years=7 / 365),
        GexLeg(695, False, 0.04, 1000, iv=0.2, tte_years=7 / 365),
        GexLeg(705, True, 0.04, 1000, iv=0.2, tte_years=7 / 365),
        GexLeg(710, True, 0.04, 4000, iv=0.2, tte_years=7 / 365),
    ]
    prof = compute_gex(legs, spot=700.0)
    if prof.flip_point is not None:
        assert prof.dist_to_flip == pytest.approx(prof.flip_point - 700.0)
        assert prof.spot_vs_flip in {"above", "below"}
