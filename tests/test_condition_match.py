"""A mission refuses aero and engine results computed at another cruise point."""

from __future__ import annotations

from nseg_mcp.cpacs_adapter import run_adapter
from tests.cpacs_fixture import WEIGHT_KG, make_cpacs

FT_TO_M = 0.3048


def _profile(mach: float, alt_ft: float = 35_000.0) -> dict:
    return {
        "range_m": 2_000_000.0,
        "weight_kg": WEIGHT_KG,
        "cruise_mach": mach,
        "cruise_altitude_m": alt_ft * FT_TO_M,
    }


def test_matching_cruise_point_runs_and_says_what_it_checked() -> None:
    xml = make_cpacs(aero_mach=0.78, engine_mach=0.78, engine_altitude_ft=35000.0)
    _, res = run_adapter(xml, _profile(0.78))
    assert res["success"] is True
    assert res["condition_check"] == "cruise point matches aero at Mach 0.78, engine at Mach 0.78"


def test_polar_from_another_mach_is_refused() -> None:
    # The dry-run case: SU2 last ran at 0.70, the mission asks for 0.78.
    xml = make_cpacs(aero_mach=0.70, engine_mach=0.78, engine_altitude_ft=35000.0)
    out_xml, res = run_adapter(xml, _profile(0.78))
    assert res["success"] is False
    assert res["error"]["type"] == "inconsistent_inputs"
    assert "SU2 at Mach 0.7" in res["error"]["message"]
    assert out_xml == xml  # nothing written


def test_engine_from_another_mach_or_altitude_is_refused() -> None:
    _, res = run_adapter(make_cpacs(aero_mach=0.78, engine_mach=0.70), _profile(0.78))
    assert res["error"]["type"] == "inconsistent_inputs"
    assert "pyCycle at Mach 0.7" in res["error"]["message"]

    xml = make_cpacs(aero_mach=0.78, engine_mach=0.78, engine_altitude_ft=30000.0)
    _, res = run_adapter(xml, _profile(0.78, 35_000.0))
    assert res["error"]["type"] == "inconsistent_inputs"
    assert "30,000 ft" in res["error"]["message"]


def test_results_that_state_no_condition_run_and_say_so() -> None:
    _, res = run_adapter(make_cpacs(), _profile(0.78))
    assert res["success"] is True
    assert "not checked: aero (no Mach recorded), engine (no Mach recorded)" in res["condition_check"]


def test_rounding_within_tolerance_is_accepted() -> None:
    # The agent converts feet to metres and back; the harnesses use 0.3048.
    xml = make_cpacs(aero_mach=0.78, engine_mach=0.78, engine_altitude_ft=35000.0)
    _, res = run_adapter(xml, {**_profile(0.78), "cruise_altitude_m": 35000.0 / 3.28084})
    assert res["success"] is True
