"""The ATA Standard Mission Rules profile: mission, reserves, gaps, and the
default profile left exactly as it was."""

from __future__ import annotations

import math
from xml.etree import ElementTree as ET

import pytest

from nseg_mcp.ata_mission import (
    FT_M,
    KT_M_S,
    PASSENGER_ALLOWANCE_KG,
    PATTERN_ALTITUDE_M,
    SCREEN_HEIGHT_M,
    SPEED_LIMIT_ALTITUDE_M,
)
from nseg_mcp.cpacs_adapter import _build_default_segments, read_from_cpacs, run_adapter
from nseg_mcp.physics.atmosphere import G0, isa
from nseg_mcp.physics.segments import hold_segment, min_drag_speed, taxi_segment
from nseg_mcp.tools.create_mission import close_mission, create_mission
from nseg_mcp.tools.run_mission import run_mission
from nseg_mcp.tools.set_segments import set_segments
from nseg_mcp.tools.set_vehicle import set_vehicle
from tests.cpacs_fixture import CD0, FN_N, REF_AREA_M2, TSFC_1_PER_S, WEIGHT_KG, K, make_cpacs

ATA_PROFILE = {
    "rules": "ATA",
    "weight_kg": WEIGHT_KG,
    "cruise_mach": 0.78,
    "cruise_altitude_m": 10_668.0,
    "range_m": 3_000_000.0,
    "diversion_altitude_ft": 25_000.0,
    "taxi_fuel_flow_kg_s": 0.2,
}

RESERVE_PARTS = (
    "reserve_five_percent_kg",
    "missed_approach_kg",
    "diversion_climb_kg",
    "diversion_cruise_kg",
    "diversion_descent_kg",
    "diversion_approach_landing_kg",
    "hold_kg",
    "taxi_in_kg",
)


def _without(profile: dict, key: str) -> dict:
    return {k: v for k, v in profile.items() if k != key}


def _mission_node(xml: str) -> ET.Element:
    node = ET.fromstring(xml).find(".//vehicles/aircraft/model/analysisResults/mission")
    assert node is not None
    return node


# --- hold segment -------------------------------------------------------------


def test_hold_fuel_is_drag_times_tsfc_times_time_at_the_minimum_drag_point() -> None:
    w, alt, t = 60_000.0, PATTERN_ALTITUDE_M, 1800.0
    seg = hold_segment(
        weight_kg=w, altitude_m=alt, duration_s=t, cd0=CD0, k=K, wing_area_m2=REF_AREA_M2, tsfc_1_per_s=TSFC_1_PER_S
    )

    cl_md = math.sqrt(CD0 / K)
    cd_md = CD0 + K * cl_md * cl_md  # induced drag equals zero-lift drag here
    drag = w * G0 * cd_md / cl_md
    assert seg.fuel_burned_kg == pytest.approx(drag * TSFC_1_PER_S * t, rel=1e-12)

    # The same drag via the speed route: lift = weight at CL_md fixes V, and q*S*CD_md is the drag.
    v = min_drag_speed(w, alt, CD0, K, REF_AREA_M2)
    q = 0.5 * isa(alt).density_kg_m3 * v * v
    assert q * REF_AREA_M2 * cl_md == pytest.approx(w * G0, rel=1e-12)
    assert q * REF_AREA_M2 * cd_md == pytest.approx(drag, rel=1e-12)
    assert 150.0 < v / KT_M_S < 300.0  # a transport holds around 200-250 kt

    assert seg.segment_type == "hold"
    assert seg.time_s == t
    assert seg.distance_m == 0.0
    assert seg.start_altitude_m == seg.end_altitude_m == alt
    assert seg.end_weight_kg == pytest.approx(w - seg.fuel_burned_kg)


def test_hold_refuses_a_polar_without_a_minimum_drag_point() -> None:
    with pytest.raises(ValueError):
        hold_segment(
            60_000.0, PATTERN_ALTITUDE_M, 1800.0, cd0=CD0, k=0.0, wing_area_m2=REF_AREA_M2, tsfc_1_per_s=TSFC_1_PER_S
        )
    with pytest.raises(ValueError):
        min_drag_speed(60_000.0, PATTERN_ALTITUDE_M, cd0=-0.01, k=K, wing_area_m2=REF_AREA_M2)


def test_hold_is_a_segment_type_in_the_tool_layer() -> None:
    sid = create_mission({"name": "hold"})["session_id"]
    try:
        set_vehicle(
            {
                "session_id": sid,
                "weight_kg": WEIGHT_KG,
                "wing_area_m2": REF_AREA_M2,
                "cd0": CD0,
                "k": K,
                "tsfc_1_per_s": TSFC_1_PER_S,
                "max_thrust_n": FN_N,
            }
        )
        set_segments(
            {
                "session_id": sid,
                "segments": [
                    {
                        "type": "hold",
                        "start_altitude_m": PATTERN_ALTITUDE_M,
                        "end_altitude_m": PATTERN_ALTITUDE_M,
                        "duration_s": 1800,
                    },
                    {"type": "taxi", "duration_s": 600, "fuel_flow_kg_s": 0.2},
                ],
            }
        )
        result = run_mission({"session_id": sid})
    finally:
        close_mission({"session_id": sid})

    assert result["success"] is True
    expected = hold_segment(WEIGHT_KG, PATTERN_ALTITUDE_M, 1800.0, CD0, K, REF_AREA_M2, TSFC_1_PER_S)
    assert result["segments"][0]["segment_type"] == "hold"
    assert result["segments"][0]["fuel_burned_kg"] == pytest.approx(expected.fuel_burned_kg)
    assert result["segments"][1]["fuel_burned_kg"] == pytest.approx(0.2 * 600)


def test_taxi_with_an_explicit_fuel_flow_is_flow_times_time_and_without_one_is_unchanged() -> None:
    explicit = taxi_segment(WEIGHT_KG, 600.0, TSFC_1_PER_S, fuel_flow_kg_s=0.2)
    assert explicit.fuel_burned_kg == pytest.approx(120.0)
    convention = taxi_segment(WEIGHT_KG, 300.0, TSFC_1_PER_S)
    assert convention.fuel_burned_kg == pytest.approx(
        min(0.07 * WEIGHT_KG * G0 * TSFC_1_PER_S * 300.0, 0.1 * WEIGHT_KG)
    )


# --- the ATA mission ----------------------------------------------------------


def test_ata_run_with_taxi_fuel_flow_is_complete_and_every_reserve_part_is_positive() -> None:
    out_xml, res = run_adapter(make_cpacs(), ATA_PROFILE)

    assert res["success"] is True
    assert res["rules"] == "ATA"
    assert res["block_fuel_complete"] is True
    assert res["gaps"] == []

    for key in RESERVE_PARTS:
        assert res[key] > 0.0, key
    assert res["reserve_total_kg"] == pytest.approx(sum(res[key] for key in RESERVE_PARTS))
    assert res["reserve_five_percent_kg"] == pytest.approx(0.05 * res["trip_fuel_kg"])

    assert res["taxi_out_kg"] == pytest.approx(0.2 * 600.0)
    assert res["taxi_in_kg"] == pytest.approx(0.2 * 300.0)
    assert res["block_fuel_kg"] == pytest.approx(res["taxi_out_kg"] + res["trip_fuel_kg"] + res["taxi_in_kg"])
    assert res["total_fuel_burned_kg"] == pytest.approx(res["block_fuel_kg"])
    assert res["fuel_load_kg"] == pytest.approx(res["taxi_out_kg"] + res["trip_fuel_kg"] + res["reserve_total_kg"])
    assert res["final_weight_kg"] == pytest.approx(WEIGHT_KG - res["block_fuel_kg"])

    # Still-air range spans climb-out to top of approach and equals the requested range.
    assert res["still_air_range_m"] == pytest.approx(ATA_PROFILE["range_m"], rel=1e-9)
    assert res["cruise_time_s"] >= 600.0
    assert res["cruise_distance_m"] < ATA_PROFILE["range_m"]

    # Trip fuel and time span take-off to landing, both taxis excluded.
    trip = [s for s in res["segments"] if s["name"] not in ("taxi_out", "taxi_in")]
    assert res["trip_fuel_kg"] == pytest.approx(sum(s["fuel_burned_kg"] for s in trip))
    assert res["flight_time_s"] == pytest.approx(sum(s["time_s"] for s in trip))

    names = [s["name"] for s in res["segments"]]
    assert names[0] == "taxi_out" and names[1] == "takeoff" and names[-2] == "landing" and names[-1] == "taxi_in"
    assert "climb_250kt_to_10000ft" in names
    assert any(n.startswith("climb_M0.60_to_") for n in names)
    assert "cruise" in names
    assert any(n.startswith("descent_M0.50_to_10000ft") for n in names)
    assert "descent_250kt_to_1500ft" in names

    reserve_names = [s["name"] for s in res["reserve_segments"]]
    assert reserve_names == [
        "missed_approach",
        "diversion_climb_250kt_to_10000ft",
        "diversion_climb_M0.60_to_25000ft",
        "diversion_cruise_200nm",
        "diversion_descent_M0.50_to_10000ft",
        "diversion_descent_250kt_to_1500ft",
        "hold_30min_1500ft",
        "diversion_approach",
        "diversion_landing",
    ]
    cruise_200 = next(s for s in res["reserve_segments"] if s["name"] == "diversion_cruise_200nm")
    assert cruise_200["distance_m"] == pytest.approx(200.0 * 1852.0)
    assert cruise_200["start_altitude_m"] == pytest.approx(25_000.0 * FT_M)
    hold = next(s for s in res["reserve_segments"] if s["name"] == "hold_30min_1500ft")
    assert hold["time_s"] == 1800.0 and hold["start_altitude_m"] == pytest.approx(PATTERN_ALTITUDE_M)

    # Reserves start from the destination landing weight.
    landing = next(s for s in res["segments"] if s["name"] == "landing")
    assert res["reserve_segments"][0]["start_weight_kg"] == pytest.approx(landing["start_weight_kg"])

    for phrase in ("winds", "long-range-cruise", "IAS/TAS", "step cruise", "standard-day"):
        assert phrase in res["rules_note"]

    # Sane magnitudes for a 70 t narrow-body over 3,000 km.
    assert 0.05 * WEIGHT_KG < res["trip_fuel_kg"] < 0.30 * WEIGHT_KG
    assert res["reserve_total_kg"] < res["trip_fuel_kg"]
    assert "thrust_closure" in res

    node = _mission_node(out_xml)
    assert node.findtext("backend") == "nseg"


def test_two_leg_climb_flies_250kt_true_airspeed_below_10000ft() -> None:
    _, res = run_adapter(make_cpacs(), ATA_PROFILE)
    leg = next(s for s in res["segments"] if s["name"] == "climb_250kt_to_10000ft")
    assert leg["start_altitude_m"] == pytest.approx(SCREEN_HEIGHT_M)
    assert leg["end_altitude_m"] == pytest.approx(SPEED_LIMIT_ALTITUDE_M)
    # Mean horizontal speed over the leg is 250 kt to within the flight-path
    # angle and the speed-of-sound change over the band.
    assert leg["distance_m"] / leg["time_s"] == pytest.approx(250.0 * KT_M_S, rel=0.01)

    upper = next(s for s in res["segments"] if s["name"].startswith("climb_M0.60_to_"))
    assert upper["start_altitude_m"] == pytest.approx(SPEED_LIMIT_ALTITUDE_M)
    assert upper["end_altitude_m"] == pytest.approx(ATA_PROFILE["cruise_altitude_m"])


def test_ata_results_are_written_to_cpacs_with_the_honesty_flags() -> None:
    out_xml, res = run_adapter(make_cpacs(), ATA_PROFILE)
    node = _mission_node(out_xml)

    assert node.findtext("rules") == "ATA"
    assert node.findtext("blockFuelComplete") == "true"
    assert float(node.findtext("tripFuelKg")) == pytest.approx(res["trip_fuel_kg"])
    assert float(node.findtext("blockFuelKg")) == pytest.approx(res["block_fuel_kg"])
    assert float(node.findtext("stillAirRangeM")) == pytest.approx(res["still_air_range_m"])
    assert float(node.findtext("totalFuelBurnedKg")) == pytest.approx(res["block_fuel_kg"])

    reserves = node.find("reserves")
    assert reserves is not None
    assert float(reserves.findtext("reserveFivePercentKg")) == pytest.approx(res["reserve_five_percent_kg"])
    assert float(reserves.findtext("holdKg")) == pytest.approx(res["hold_kg"])
    assert float(reserves.findtext("taxiInKg")) == pytest.approx(res["taxi_in_kg"])
    assert float(reserves.findtext("reserveTotalKg")) == pytest.approx(res["reserve_total_kg"])

    gaps = node.find("gaps")
    assert gaps is not None and len(gaps) == 0
    assert node.findtext("rulesNote") == res["rules_note"]
    assert len(node.find("reserveSegments")) == len(res["reserve_segments"])
    assert node.find("segments/segment/name").text == "taxi_out"


def test_ata_without_taxi_fuel_flow_lists_the_gap_and_flags_block_fuel_incomplete() -> None:
    out_xml, res = run_adapter(make_cpacs(), _without(ATA_PROFILE, "taxi_fuel_flow_kg_s"))

    assert res["success"] is True
    assert res["block_fuel_complete"] is False
    assert any(g.startswith("taxi_out_10min: no idle fuel flow available") for g in res["gaps"])
    assert any(g.startswith("taxi_in_5min: no idle fuel flow available") for g in res["gaps"])
    assert res["taxi_out_kg"] is None and res["taxi_in_kg"] is None
    assert all(s["segment_type"] != "taxi" for s in res["segments"])
    assert res["block_fuel_kg"] == pytest.approx(res["trip_fuel_kg"])
    assert res["reserve_total_kg"] == pytest.approx(sum(res[k] for k in RESERVE_PARTS if k != "taxi_in_kg"))

    node = _mission_node(out_xml)
    assert node.findtext("blockFuelComplete") == "false"
    assert [g.text for g in node.find("gaps")] == res["gaps"]
    assert node.find("reserves/taxiInKg") is None
    assert node.find("taxiOutKg") is None
    assert "block fuel incomplete" in ET.fromstring(out_xml).findall("header/updates/update")[-1].findtext(
        "modification"
    )


def test_ata_requires_a_diversion_altitude_and_writes_nothing_without_it() -> None:
    xml = make_cpacs()
    out_xml, res = run_adapter(xml, _without(ATA_PROFILE, "diversion_altitude_ft"))
    assert res["success"] is False
    assert res["error"]["type"] == "missing_input"
    assert "diversion_altitude_ft" in res["error"]["message"]
    assert out_xml == xml


def test_ata_refuses_a_diversion_altitude_at_or_below_the_pattern() -> None:
    _, res = run_adapter(make_cpacs(), {**ATA_PROFILE, "diversion_altitude_ft": 1000.0})
    assert res["success"] is False and res["error"]["type"] == "invalid_input"


def test_ata_refuses_a_range_too_short_for_ten_minutes_of_cruise() -> None:
    _, res = run_adapter(make_cpacs(), {**ATA_PROFILE, "range_m": 300_000.0})
    assert res["success"] is False
    assert res["error"]["type"] == "infeasible_mission"


def test_ata_refuses_a_caller_segment_list() -> None:
    _, res = run_adapter(make_cpacs(), {**ATA_PROFILE, "segments": [{"type": "taxi", "duration_s": 60}]})
    assert res["success"] is False and res["error"]["type"] == "invalid_input"


def test_unknown_rules_are_refused() -> None:
    xml = make_cpacs()
    out_xml, res = run_adapter(xml, {**ATA_PROFILE, "rules": "FAR121"})
    assert res["success"] is False and res["error"]["type"] == "invalid_input"
    assert out_xml == xml


def test_rules_value_is_case_insensitive() -> None:
    _, res = run_adapter(make_cpacs(), {**ATA_PROFILE, "rules": "ata"})
    assert res["success"] is True and res["rules"] == "ATA"


# --- passengers and payload ---------------------------------------------------


def test_passengers_become_payload_at_225_lb_each() -> None:
    out_xml, res = run_adapter(make_cpacs(), {**ATA_PROFILE, "passengers": 150})
    assert PASSENGER_ALLOWANCE_KG == pytest.approx(225.0 * 0.45359237)
    assert res["payload_kg"] == pytest.approx(150 * 225.0 * 0.45359237)
    assert res["payload_kg"] == pytest.approx(15_308.74, abs=0.01)
    assert res["payload_source"] == "ATA 225 lb per passenger"

    node = _mission_node(out_xml)
    assert float(node.findtext("payloadKg")) == pytest.approx(res["payload_kg"])
    assert node.findtext("payloadSource") == "ATA 225 lb per passenger"


def test_explicit_payload_wins_and_no_passenger_count_is_ever_assumed() -> None:
    _, res = run_adapter(make_cpacs(), {**ATA_PROFILE, "passengers": 150, "payload_kg": 12_000.0})
    assert res["payload_kg"] == 12_000.0
    assert res["payload_source"] == "mission_profile:payload_kg"

    out_xml, res = run_adapter(make_cpacs(), ATA_PROFILE)
    assert "payload_kg" not in res and "payload_source" not in res
    assert _mission_node(out_xml).find("payloadKg") is None


# --- the default profile is untouched -----------------------------------------


def test_default_profile_is_unchanged_without_rules() -> None:
    profile = {"weight_kg": WEIGHT_KG, "cruise_mach": 0.78, "cruise_altitude_m": 10_668.0, "range_m": 3_000_000.0}
    out_xml, res = run_adapter(make_cpacs(), profile)

    assert res["success"] is True
    assert [s["segment_type"] for s in res["segments"]] == [
        "taxi",
        "takeoff",
        "climb",
        "cruise",
        "descent",
        "approach",
        "landing",
    ]
    for key in ("rules", "gaps", "reserve_total_kg", "block_fuel_complete", "reserve_segments", "trip_fuel_kg"):
        assert key not in res

    # Same numbers as the tool path flying the same default segment list.
    inputs = read_from_cpacs(make_cpacs(), profile)
    sid = create_mission({"name": "reference"})["session_id"]
    try:
        set_vehicle(
            {
                "session_id": sid,
                "weight_kg": WEIGHT_KG,
                "wing_area_m2": inputs["ref_area_m2"],
                "cd0": inputs["cd0"],
                "k": inputs["k"],
                "tsfc_1_per_s": inputs["tsfc_1_per_s"],
                "max_thrust_n": inputs["max_thrust_n"],
            }
        )
        set_segments({"session_id": sid, "segments": _build_default_segments(inputs)})
        reference = run_mission({"session_id": sid})
    finally:
        close_mission({"session_id": sid})
    assert res["total_fuel_burned_kg"] == pytest.approx(reference["total_fuel_burned_kg"], rel=1e-12)
    assert res["total_distance_m"] == pytest.approx(reference["total_distance_m"], rel=1e-12)

    node = _mission_node(out_xml)
    assert node.find("rules") is None
    assert node.find("reserves") is None
    assert node.find("gaps") is None
    assert node.find("segments/segment/name") is None
    assert len(node.findall("segments/segment")) == 7
