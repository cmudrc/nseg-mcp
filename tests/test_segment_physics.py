"""Regression tests for two physics defects found on 2026-09-10.

Both were silent: the block-fuel total looked plausible because the two
errors pulled in opposite directions. Neither is a stub in the no-stubs sense;
both are wrong physics that the tests here pin down.
"""

from __future__ import annotations

import math

import pytest

from nseg_mcp.physics.segments import (
    G0,
    _lift_to_drag,
    climb_segment,
    cruise_segment,
    mach_to_tas,
)

W = 70_000.0
S = 122.4
CD0, K = 0.0164, 0.0398  # D150 polar, aspect ratio from the file's geometry
TSFC = 1.7e-5  # kg/(N*s), mass-based, as the propulsion stage supplies
ALT = 10_668.0  # 35,000 ft
MACH = 0.78
RANGE = 3_000_000.0


def test_cruise_breguet_includes_g0_for_mass_based_tsfc() -> None:
    """m_end = m_start * exp(-R * tsfc * g0 / (V * L/D)) when tsfc is kg/(N*s)."""
    seg = cruise_segment(
        weight_kg=W,
        altitude_m=ALT,
        mach=MACH,
        distance_m=RANGE,
        cd0=CD0,
        k=K,
        wing_area_m2=S,
        tsfc_1_per_s=TSFC,
    )
    v = mach_to_tas(MACH, ALT)
    ld = _lift_to_drag(W, CD0, K, S, MACH, ALT)

    expected = W * (1.0 - math.exp(-RANGE * TSFC * G0 / (v * ld)))
    assert seg.fuel_burned_kg == pytest.approx(expected, rel=1e-9)

    # The pre-fix form omitted g0. With a mass-based TSFC that understates
    # cruise fuel by roughly a factor of g0; make sure we never go back to it.
    without_g0 = W * (1.0 - math.exp(-RANGE * TSFC / (v * ld)))
    assert seg.fuel_burned_kg > 5.0 * without_g0


def test_cruise_fuel_is_a_sane_fraction_for_a_transport() -> None:
    """3,000 km at M0.78 for a 70 t narrow-body: a few tonnes, not a few hundred kg."""
    seg = cruise_segment(
        weight_kg=W,
        altitude_m=ALT,
        mach=MACH,
        distance_m=RANGE,
        cd0=CD0,
        k=K,
        wing_area_m2=S,
        tsfc_1_per_s=TSFC,
    )
    assert 0.05 * W < seg.fuel_burned_kg < 0.30 * W


def test_climb_time_is_altitude_over_rate_of_climb() -> None:
    """The energy method integrates dt = dh / ROC, so total time is exact."""
    roc = 7.62
    seg = climb_segment(
        weight_kg=W,
        start_altitude_m=0.0,
        end_altitude_m=ALT,
        mach=0.5,
        cd0=CD0,
        k=K,
        wing_area_m2=S,
        tsfc_1_per_s=TSFC,
        roc_m_s=roc,
    )
    assert seg.time_s == pytest.approx(ALT / roc, rel=1e-9)


def test_climb_fuel_is_bounded_and_dimensionally_sane() -> None:
    """Pre-fix the thrust term had units of N*s and climb fuel came out several
    times too high. With T = D + W*g0*sin(gamma) a transport climb to FL350
    burns on the order of a tonne, and never a large fraction of the aircraft.
    """
    seg = climb_segment(
        weight_kg=W,
        start_altitude_m=0.0,
        end_altitude_m=ALT,
        mach=0.5,
        cd0=CD0,
        k=K,
        wing_area_m2=S,
        tsfc_1_per_s=TSFC,
        roc_m_s=7.62,
    )
    assert 0.0 < seg.fuel_burned_kg < 0.04 * W
    assert seg.end_weight_kg == pytest.approx(W - seg.fuel_burned_kg)


def test_climb_rate_is_a_documented_overridable_assumption() -> None:
    """Doubling the rate of climb halves the time; it is not hardcoded inside."""
    slow = climb_segment(
        weight_kg=W,
        start_altitude_m=0.0,
        end_altitude_m=ALT,
        mach=0.5,
        cd0=CD0,
        k=K,
        wing_area_m2=S,
        tsfc_1_per_s=TSFC,
        roc_m_s=5.0,
    )
    fast = climb_segment(
        weight_kg=W,
        start_altitude_m=0.0,
        end_altitude_m=ALT,
        mach=0.5,
        cd0=CD0,
        k=K,
        wing_area_m2=S,
        tsfc_1_per_s=TSFC,
        roc_m_s=10.0,
    )
    assert slow.time_s == pytest.approx(2.0 * fast.time_s, rel=1e-9)
