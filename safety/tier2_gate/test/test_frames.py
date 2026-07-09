"""_frames.py — NED↔ENU + battery 정규화 단위 테스트."""

from __future__ import annotations

import math

import pytest

from tier2_gate._frames import battery_remaining_to_pct, ned_to_enu


# ---- NED ↔ ENU ----

def test_ned_to_enu_basic_axes():
    # NED (1, 0, 0) = North → ENU (0, 1, 0) = North
    assert ned_to_enu(1.0, 0.0, 0.0) == (0.0, 1.0, 0.0)
    # NED (0, 1, 0) = East → ENU (1, 0, 0) = East
    assert ned_to_enu(0.0, 1.0, 0.0) == (1.0, 0.0, 0.0)
    # NED (0, 0, 1) = Down → ENU (0, 0, -1) = Down
    assert ned_to_enu(0.0, 0.0, 1.0) == (0.0, 0.0, -1.0)


def test_ned_enu_is_involution():
    """ENU↔NED 가 같은 사상 — 두 번 적용하면 원래 값."""
    cases = [(1.0, 2.0, 3.0), (-0.5, 0.7, -1.2), (0.0, 0.0, 0.0)]
    for x, y, z in cases:
        once = ned_to_enu(x, y, z)
        twice = ned_to_enu(*once)
        assert twice == (x, y, z)


def test_ned_to_enu_z_sign_flip():
    """z 부호 반전 — NED 양수 z (아래) = ENU 음수 z (아래)."""
    _, _, z_enu = ned_to_enu(0.0, 0.0, 5.0)
    assert z_enu == -5.0


# ---- battery ----

def test_battery_full():
    assert battery_remaining_to_pct(1.0) == 100.0


def test_battery_empty():
    assert battery_remaining_to_pct(0.0) == 0.0


def test_battery_half():
    assert battery_remaining_to_pct(0.5) == 50.0


def test_battery_nan_returns_zero():
    """PX4 가 NaN 보고 시 보수적으로 0% — Φ_5 가 즉시 RTL 강제."""
    assert battery_remaining_to_pct(float('nan')) == 0.0


def test_battery_above_one_clamps_to_100():
    assert battery_remaining_to_pct(1.5) == 100.0


def test_battery_negative_clamps_to_zero():
    assert battery_remaining_to_pct(-0.1) == 0.0
