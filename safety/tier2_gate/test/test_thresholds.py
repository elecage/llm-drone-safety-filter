"""ADR-0013 D4·D5 + ADR-0019 D4 운용 임계 단위 테스트."""

from __future__ import annotations

import pytest

from tier2_gate.thresholds import DEFAULT, Thresholds


# ---- 1차 시안 값 잠금 (ADR과 동기 강제) ----

def test_default_c_thresholds_match_adr_0013_d4():
    assert DEFAULT.c_lo == 0.4
    assert DEFAULT.c_hi == 0.7


def test_default_operational_thresholds_match_adr_0013_d5():
    assert DEFAULT.B_rtl == 30.0
    assert DEFAULT.T_link == 3.0
    assert DEFAULT.N_sc == 3
    assert DEFAULT.T_resp == 30.0


def test_default_contradicts_thresholds_match_adr_0019_d4():
    assert DEFAULT.D_cancel == 0.5
    assert DEFAULT.eps_vp == 0.1
    assert DEFAULT.tau_settle == 1.0
    assert DEFAULT.eps_dock == 0.2


# ---- 불변식 (생성 시 강제) ----

def test_c_lo_must_not_exceed_c_hi():
    with pytest.raises(AssertionError):
        Thresholds(c_lo=0.8, c_hi=0.5)


def test_c_lo_must_be_nonneg():
    with pytest.raises(AssertionError):
        Thresholds(c_lo=-0.1)


def test_c_hi_must_not_exceed_one():
    with pytest.raises(AssertionError):
        Thresholds(c_hi=1.1)


def test_N_sc_must_be_positive():
    with pytest.raises(AssertionError):
        Thresholds(N_sc=0)


def test_T_resp_must_be_positive():
    with pytest.raises(AssertionError):
        Thresholds(T_resp=0.0)


def test_D_cancel_must_be_positive():
    with pytest.raises(AssertionError):
        Thresholds(D_cancel=0.0)


def test_paper_C_sweep_grid_is_constructible():
    """ADR-0013 D4 paper §C sweep: c_lo ∈ {0.3, 0.4, 0.5}, c_hi ∈ {0.6, 0.7, 0.8}."""
    for c_lo in (0.3, 0.4, 0.5):
        for c_hi in (0.6, 0.7, 0.8):
            Thresholds(c_lo=c_lo, c_hi=c_hi)
