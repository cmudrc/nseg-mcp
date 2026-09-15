"""ATA Standard Mission Rules profile for the shared-CPACS NSEG adapter.

Builds the mission and its reserves from the segment functions in
:mod:`nseg_mcp.physics.segments`, following the ATA Standard Mission Rules
slide Boeing shared with us (transcribed 2026-09-14):

Mission (flown with 85% annual headwinds): taxi out 10 min; take-off to
35 ft; climb-out and accelerate to 1,500 ft and 250 kt; climb at 250 kt to
10,000 ft; accelerate to climb speed; climb to cruise altitude; step cruise
at long-range-cruise speed; descend to 10,000 ft; decelerate to 250 kt;
descend to 1,500 ft at 250 kt; approach and land; taxi in 5 min, charged to
reserves. Still-air range spans climb-out to top of approach; flight time
and fuel span take-off to landing; block adds both taxis.

Reserves (zero wind): 5% of the trip fuel; missed approach; climb; 200 nm
cruise to an alternate; descend to 1,500 ft; approach and land; 30-minute
hold at 1,500 ft.

Ground rules: standard day; 225 lb per passenger; fuel 6.7 lb per US
gallon; RVSM cruise altitudes; at least 10 min en-route cruise.

What is simplified is listed once in :data:`RULES_NOTE` and returned with
every result. Anything that cannot be computed honestly -- today the taxi
legs, which need an idle fuel flow the propulsion stage does not provide --
is left out and named in ``gaps`` rather than estimated.
"""

from __future__ import annotations

from typing import Any

from nseg_mcp.physics.atmosphere import tas_to_mach
from nseg_mcp.physics.segments import (
    APPROACH_DURATION_S,
    APPROACH_THRUST_FRACTION,
    TAXI_THRUST_FRACTION,
    SegmentResult,
    approach_segment,
    climb_segment,
    cruise_segment,
    descent_segment,
    hold_segment,
    landing_segment,
    min_drag_speed,
    takeoff_segment,
    taxi_segment,
)
from nseg_mcp.tools.run_mission import _thrust_closure

# Unit factors, exact by definition.
FT_M = 0.3048
NM_M = 1852.0
KT_M_S = NM_M / 3600.0
LB_KG = 0.45359237

# The profile's fixed points, from the slide.
SCREEN_HEIGHT_M = 35.0 * FT_M
PATTERN_ALTITUDE_M = 1500.0 * FT_M
SPEED_LIMIT_ALTITUDE_M = 10_000.0 * FT_M
SPEED_LIMIT_TAS_M_S = 250.0 * KT_M_S
TAXI_OUT_S = 10.0 * 60.0
TAXI_IN_S = 5.0 * 60.0
HOLD_S = 30.0 * 60.0
DIVERSION_DISTANCE_M = 200.0 * NM_M
MIN_CRUISE_TIME_S = 10.0 * 60.0
RESERVE_FRACTION_OF_TRIP = 0.05
PASSENGER_ALLOWANCE_KG = 225.0 * LB_KG
PAYLOAD_SOURCE_ATA = "ATA 225 lb per passenger"

# Mission conditions with documented defaults. They describe how the
# manoeuvre is flown, not the aircraft, so the caller may override them
# (``climb_mach``, ``descent_mach``, ``climb_roc_m_s`` in the profile). The
# caps match the adapter's default profile.
DEFAULT_CLIMB_MACH_CAP = 0.6
DEFAULT_DESCENT_MACH_CAP = 0.5
DEFAULT_ROC_M_S = 7.62  # 1,500 ft/min, the climb_segment default

GAP_TAXI_OUT = (
    "taxi_out_10min: no idle fuel flow available (pass taxi_fuel_flow_kg_s in the "
    "mission profile; the propulsion stage does not provide an idle point yet)"
)
GAP_TAXI_IN = (
    "taxi_in_5min: no idle fuel flow available (pass taxi_fuel_flow_kg_s in the "
    "mission profile; the propulsion stage does not provide an idle point yet)"
)

RULES_NOTE = (
    "ATA Standard Mission Rules profile with these simplifications: winds are not "
    "modelled, so the mission is flown still-air and the ATA 85% annual headwind is "
    "not applied; the cruise and the 200 nm diversion are flown at the stated cruise "
    "Mach because long-range-cruise speed is not modelled; the 250 kt legs are flown "
    "at the Mach for 250 kt true airspeed at each leg's mid-altitude because IAS/TAS "
    "conversion is not modelled; acceleration and deceleration are not modelled, each "
    "leg is flown at constant Mach; the cruise is a single altitude, step cruise is not "
    "modelled and RVSM flight levels are not enforced; the missed approach is flown as "
    "a climb from the runway to 1,500 ft at 250 kt; standard-day ISA atmosphere "
    "throughout, as the ground rules require."
)

Leg = tuple[str, SegmentResult]


def check_ata_inputs(inputs: dict[str, Any]) -> dict[str, Any] | None:
    """Return a structured error for anything the ATA profile cannot proceed without."""
    if inputs.get("segments"):
        return {
            "type": "invalid_input",
            "message": "rules == 'ATA' builds its own segment list; do not pass segments as well.",
            "details": "Drop segments from the mission profile, or drop rules to fly the caller's segments.",
        }

    diversion_ft = inputs.get("diversion_altitude_ft")
    if diversion_ft is None:
        return {
            "type": "missing_input",
            "message": "Cannot fly the ATA reserves: diversion_altitude_ft not available.",
            "details": (
                "The ATA slide fixes the 200 nm diversion distance but not the altitude it "
                "is flown at; that is a mission choice, so the caller must state it. Pass "
                "diversion_altitude_ft in the mission profile. It is not defaulted, because "
                "the reserve fuel would then rest on a number nobody chose."
            ),
        }
    try:
        diversion_m = float(diversion_ft) * FT_M
    except (TypeError, ValueError):
        return {"type": "invalid_input", "message": f"diversion_altitude_ft must be a number; got {diversion_ft!r}."}
    if diversion_m <= PATTERN_ALTITUDE_M:
        return {
            "type": "invalid_input",
            "message": f"diversion_altitude_ft must be above the 1,500 ft pattern altitude; got {float(diversion_ft):g} ft.",
        }

    cruise_altitude_m = float(inputs["cruise_altitude_m"])
    if cruise_altitude_m <= PATTERN_ALTITUDE_M:
        return {
            "type": "invalid_input",
            "message": f"cruise_altitude_m must be above the 1,500 ft pattern altitude; got {cruise_altitude_m:g} m.",
        }

    taxi_flow = inputs.get("taxi_fuel_flow_kg_s")
    if taxi_flow is not None and float(taxi_flow) <= 0.0:
        return {"type": "invalid_input", "message": f"taxi_fuel_flow_kg_s must be positive; got {taxi_flow!r}."}

    passengers = inputs.get("passengers")
    if passengers is not None and (float(passengers) < 0.0 or float(passengers) != int(float(passengers))):
        return {"type": "invalid_input", "message": f"passengers must be a whole number >= 0; got {passengers!r}."}

    return None


def _failure(error: dict[str, Any]) -> dict[str, Any]:
    return {"success": False, "solver": "nseg", "backend": "nseg", "rules": "ATA", "error": error}


def _override(value: Any, default: float) -> float:
    return float(value) if value is not None else default


def _ft(altitude_m: float) -> str:
    return f"{round(altitude_m / FT_M):d}ft"


def _mach_for_250kt(start_m: float, end_m: float) -> float:
    """Mach for 250 kt true airspeed at the leg's mid-altitude.

    The slide means 250 kt indicated; IAS/TAS conversion is not modelled, so
    the leg is flown at the Mach that gives 250 kt true airspeed halfway up.
    """
    return tas_to_mach(SPEED_LIMIT_TAS_M_S, 0.5 * (start_m + end_m))


def _climb_legs(
    weight_kg: float,
    start_m: float,
    end_m: float,
    climb_mach: float,
    roc_m_s: float,
    aero: dict[str, float],
    prefix: str,
) -> list[Leg]:
    """Climb in the ATA's two legs: 250 kt to 10,000 ft, then the climb Mach.

    Only the legs the altitude band actually crosses are flown, so a climb
    that stays below 10,000 ft is one 250 kt leg.
    """
    legs: list[Leg] = []
    w = weight_kg
    if start_m < SPEED_LIMIT_ALTITUDE_M:
        top = min(end_m, SPEED_LIMIT_ALTITUDE_M)
        seg = climb_segment(w, start_m, top, _mach_for_250kt(start_m, top), roc_m_s=roc_m_s, **aero)
        legs.append((f"{prefix}_250kt_to_{_ft(top)}", seg))
        w = seg.end_weight_kg
    if end_m > SPEED_LIMIT_ALTITUDE_M:
        bottom = max(start_m, SPEED_LIMIT_ALTITUDE_M)
        seg = climb_segment(w, bottom, end_m, climb_mach, roc_m_s=roc_m_s, **aero)
        legs.append((f"{prefix}_M{climb_mach:.2f}_to_{_ft(end_m)}", seg))
    return legs


def _descent_legs(
    weight_kg: float,
    start_m: float,
    end_m: float,
    descent_mach: float,
    aero: dict[str, float],
    prefix: str,
) -> list[Leg]:
    """Descend in the ATA's two legs: the descent Mach to 10,000 ft, then 250 kt."""
    legs: list[Leg] = []
    w = weight_kg
    if start_m > SPEED_LIMIT_ALTITUDE_M:
        bottom = max(end_m, SPEED_LIMIT_ALTITUDE_M)
        seg = descent_segment(w, start_m, bottom, descent_mach, **aero)
        legs.append((f"{prefix}_M{descent_mach:.2f}_to_{_ft(bottom)}", seg))
        w = seg.end_weight_kg
    if end_m < SPEED_LIMIT_ALTITUDE_M:
        top = min(start_m, SPEED_LIMIT_ALTITUDE_M)
        seg = descent_segment(w, top, end_m, _mach_for_250kt(top, end_m), **aero)
        legs.append((f"{prefix}_250kt_to_{_ft(end_m)}", seg))
    return legs


def _fuel(legs: list[Leg]) -> float:
    return sum(seg.fuel_burned_kg for _, seg in legs)


def _distance(legs: list[Leg]) -> float:
    return sum(seg.distance_m for _, seg in legs)


def _time(legs: list[Leg]) -> float:
    return sum(seg.time_s for _, seg in legs)


def _end_weight(legs: list[Leg], fallback: float) -> float:
    return legs[-1][1].end_weight_kg if legs else fallback


def _to_dicts(legs: list[Leg]) -> list[dict[str, Any]]:
    return [{"name": name, **seg.to_dict()} for name, seg in legs]


def _payload(inputs: dict[str, Any]) -> tuple[float | None, str | None]:
    """Payload and where it came from. Never assumes a passenger count."""
    if inputs.get("payload_kg") is not None:
        return float(inputs["payload_kg"]), "mission_profile:payload_kg"
    if inputs.get("passengers") is not None:
        return int(float(inputs["passengers"])) * PASSENGER_ALLOWANCE_KG, PAYLOAD_SOURCE_ATA
    return None, None


def run_ata_mission(inputs: dict[str, Any]) -> dict[str, Any]:
    """Fly the ATA mission and its reserves; return the adapter's results dict.

    The dict carries the same keys as ``run_mission`` (block fuel, distances,
    times, the per-segment table) plus the ATA accounting: ``trip_fuel_kg``,
    each reserve part, ``reserve_total_kg``, ``block_fuel_kg`` with
    ``block_fuel_complete``, ``gaps`` and ``rules_note``. ``inputs`` is the
    dict from ``read_from_cpacs`` after ``_check_required`` has passed.
    """
    error = check_ata_inputs(inputs)
    if error is not None:
        return _failure(error)

    w0 = float(inputs["weight_kg"])
    aero: dict[str, float] = {
        "cd0": float(inputs["cd0"]),
        "k": float(inputs["k"]),
        "wing_area_m2": float(inputs["ref_area_m2"]),
        "tsfc_1_per_s": float(inputs["tsfc_1_per_s"]),
    }
    tsfc = aero["tsfc_1_per_s"]
    cruise_mach = float(inputs["cruise_mach"])
    cruise_alt = float(inputs["cruise_altitude_m"])
    range_m = float(inputs["range_m"])
    climb_mach = _override(inputs.get("climb_mach"), min(cruise_mach, DEFAULT_CLIMB_MACH_CAP))
    descent_mach = _override(inputs.get("descent_mach"), min(cruise_mach, DEFAULT_DESCENT_MACH_CAP))
    roc = _override(inputs.get("climb_roc_m_s"), DEFAULT_ROC_M_S)
    diversion_alt = float(inputs["diversion_altitude_ft"]) * FT_M
    taxi_flow_in = inputs.get("taxi_fuel_flow_kg_s")
    taxi_flow = float(taxi_flow_in) if taxi_flow_in is not None else None

    gaps: list[str] = []
    mission: list[Leg] = []
    w = w0

    taxi_out: SegmentResult | None = None
    if taxi_flow is not None:
        taxi_out = taxi_segment(w, TAXI_OUT_S, tsfc, fuel_flow_kg_s=taxi_flow)
        mission.append(("taxi_out", taxi_out))
        w = taxi_out.end_weight_kg
    else:
        gaps.append(GAP_TAXI_OUT)

    try:
        takeoff = takeoff_segment(w, tsfc)
        mission.append(("takeoff", takeoff))
        w = takeoff.end_weight_kg

        climb = _climb_legs(w, SCREEN_HEIGHT_M, cruise_alt, climb_mach, roc, aero, "climb")
        mission.extend(climb)
        w = _end_weight(climb, w)

        # The ATA range runs from climb-out to top of approach, so the cruise
        # gets what is left of range_m after the climb and descent legs. The
        # descent is flown once here, from the top-of-climb weight, to measure
        # its distance (which does not depend on weight), and again after the
        # cruise for its fuel.
        descent_probe = _descent_legs(w, cruise_alt, PATTERN_ALTITUDE_M, descent_mach, aero, "descent")
        cruise_distance = range_m - _distance(climb) - _distance(descent_probe)
        if cruise_distance <= 0.0:
            return _failure(
                {
                    "type": "infeasible_mission",
                    "message": (
                        f"range_m = {range_m:.0f} m is shorter than the climb and descent alone "
                        f"({_distance(climb) + _distance(descent_probe):.0f} m); no cruise is left."
                    ),
                    "details": "Increase range_m or lower cruise_altitude_m.",
                }
            )
        cruise = cruise_segment(w, cruise_alt, cruise_mach, cruise_distance, **aero)
        if cruise.time_s < MIN_CRUISE_TIME_S:
            return _failure(
                {
                    "type": "infeasible_mission",
                    "message": (
                        f"En-route cruise would last {cruise.time_s / 60.0:.1f} min; the ATA ground rules "
                        f"require at least {MIN_CRUISE_TIME_S / 60.0:.0f} min."
                    ),
                    "details": "Increase range_m or lower cruise_altitude_m.",
                }
            )
        mission.append(("cruise", cruise))
        w = cruise.end_weight_kg

        descent = _descent_legs(w, cruise_alt, PATTERN_ALTITUDE_M, descent_mach, aero, "descent")
        mission.extend(descent)
        w = _end_weight(descent, w)

        approach = approach_segment(w, PATTERN_ALTITUDE_M, tsfc)
        mission.append(("approach", approach))
        landing = landing_segment(approach.end_weight_kg, tsfc)
        mission.append(("landing", landing))
        w = landing.end_weight_kg

        taxi_in: SegmentResult | None = None
        if taxi_flow is not None:
            taxi_in = taxi_segment(w, TAXI_IN_S, tsfc, fuel_flow_kg_s=taxi_flow)
            mission.append(("taxi_in", taxi_in))
            w = taxi_in.end_weight_kg
        else:
            gaps.append(GAP_TAXI_IN)

        # Reserves, flown from the destination landing weight: the missed
        # approach is initiated at the end of the approach.
        reserves: list[Leg] = []
        wr = landing.start_weight_kg
        missed = climb_segment(
            wr, 0.0, PATTERN_ALTITUDE_M, _mach_for_250kt(0.0, PATTERN_ALTITUDE_M), roc_m_s=roc, **aero
        )
        reserves.append(("missed_approach", missed))
        wr = missed.end_weight_kg

        div_climb = _climb_legs(wr, PATTERN_ALTITUDE_M, diversion_alt, climb_mach, roc, aero, "diversion_climb")
        reserves.extend(div_climb)
        wr = _end_weight(div_climb, wr)

        div_cruise = cruise_segment(wr, diversion_alt, cruise_mach, DIVERSION_DISTANCE_M, **aero)
        reserves.append(("diversion_cruise_200nm", div_cruise))
        wr = div_cruise.end_weight_kg

        div_descent = _descent_legs(wr, diversion_alt, PATTERN_ALTITUDE_M, descent_mach, aero, "diversion_descent")
        reserves.extend(div_descent)
        wr = _end_weight(div_descent, wr)

        hold = hold_segment(wr, PATTERN_ALTITUDE_M, HOLD_S, **aero)
        hold_speed = min_drag_speed(wr, PATTERN_ALTITUDE_M, aero["cd0"], aero["k"], aero["wing_area_m2"])
        reserves.append(("hold_30min_1500ft", hold))
        wr = hold.end_weight_kg

        div_approach = approach_segment(wr, PATTERN_ALTITUDE_M, tsfc)
        reserves.append(("diversion_approach", div_approach))
        div_landing = landing_segment(div_approach.end_weight_kg, tsfc)
        reserves.append(("diversion_landing", div_landing))
    except ValueError as exc:
        return _failure(
            {
                "type": "unphysical_input",
                "message": str(exc),
                "details": (
                    "The ATA hold is flown at the minimum-drag point of the polar, which needs "
                    "positive cd0 and k. Check the aero stage before the mission stage."
                ),
            }
        )

    trip = [leg for leg in mission if leg[0] not in ("taxi_out", "taxi_in")]
    trip_fuel = _fuel(trip)
    still_air = [leg for leg in mission if leg[0].startswith(("climb", "cruise", "descent"))]
    still_air_range_m = _distance(still_air)

    taxi_out_kg = taxi_out.fuel_burned_kg if taxi_out is not None else None
    taxi_in_kg = taxi_in.fuel_burned_kg if taxi_in is not None else None

    reserve_five_percent = RESERVE_FRACTION_OF_TRIP * trip_fuel
    div_approach_landing = div_approach.fuel_burned_kg + div_landing.fuel_burned_kg
    reserve_total = (
        reserve_five_percent
        + missed.fuel_burned_kg
        + _fuel(div_climb)
        + div_cruise.fuel_burned_kg
        + _fuel(div_descent)
        + div_approach_landing
        + hold.fuel_burned_kg
        + (taxi_in_kg or 0.0)
    )
    block_fuel = trip_fuel + (taxi_out_kg or 0.0) + (taxi_in_kg or 0.0)
    fuel_load = (taxi_out_kg or 0.0) + trip_fuel + reserve_total
    total_distance = _distance(mission)
    total_time = _time(mission)

    results: dict[str, Any] = {
        "success": True,
        "backend": "nseg",
        "rules": "ATA",
        "rules_note": RULES_NOTE,
        # Operational assumptions inside the segment physics, named so a reader
        # of the result knows they are there. Taxi legs here always use a stated
        # idle fuel flow (or are listed as gaps), so the taxi fraction is
        # reported as "not used".
        "assumptions": {
            "approach_thrust_fraction_of_weight": APPROACH_THRUST_FRACTION,
            "approach_duration_s": APPROACH_DURATION_S,
            "taxi_thrust_fraction_of_weight": ("not used: taxi legs need a stated idle fuel flow under ATA rules"),
            "default_profile_taxi_thrust_fraction_of_weight": TAXI_THRUST_FRACTION,
        },
        "initial_weight_kg": w0,
        "final_weight_kg": w,
        "total_fuel_burned_kg": block_fuel,
        "fuel_burned_kg": block_fuel,
        "total_distance_m": total_distance,
        "total_distance_nm": total_distance / NM_M,
        "total_time_s": total_time,
        "total_time_hr": total_time / 3600.0,
        "fuel_fraction": block_fuel / w0,
        "segments": _to_dicts(mission),
        "reserve_segments": _to_dicts(reserves),
        # ATA accounting. Trip (flight) fuel and time span take-off to
        # landing; still-air range spans climb-out to top of approach; block
        # adds both taxis; taxi-in is charged to the reserves.
        "trip_fuel_kg": trip_fuel,
        "flight_time_s": _time(trip),
        "still_air_range_m": still_air_range_m,
        "still_air_range_nm": still_air_range_m / NM_M,
        "cruise_distance_m": cruise.distance_m,
        "cruise_time_s": cruise.time_s,
        "taxi_out_kg": taxi_out_kg,
        "reserve_five_percent_kg": reserve_five_percent,
        "missed_approach_kg": missed.fuel_burned_kg,
        "diversion_climb_kg": _fuel(div_climb),
        "diversion_cruise_kg": div_cruise.fuel_burned_kg,
        "diversion_descent_kg": _fuel(div_descent),
        "diversion_approach_landing_kg": div_approach_landing,
        "hold_kg": hold.fuel_burned_kg,
        "hold_speed_m_s": hold_speed,
        "taxi_in_kg": taxi_in_kg,
        "reserve_total_kg": reserve_total,
        "block_fuel_kg": block_fuel,
        "block_fuel_complete": not gaps,
        "fuel_load_kg": fuel_load,
        "gaps": gaps,
        "diversion_altitude_m": diversion_alt,
        "climb_mach": climb_mach,
        "descent_mach": descent_mach,
        "climb_roc_m_s": roc,
    }

    payload_kg, payload_source = _payload(inputs)
    if payload_kg is not None:
        results["payload_kg"] = payload_kg
        results["payload_source"] = payload_source

    closure = _thrust_closure(
        [{"type": "cruise", "start_altitude_m": cruise_alt, "end_altitude_m": cruise_alt, "mach": cruise_mach}],
        {"weight_kg": w0, "max_thrust_n": inputs.get("max_thrust_n")},
        aero["cd0"],
        aero["k"],
        aero["wing_area_m2"],
    )
    if closure is not None:
        results["thrust_closure"] = closure
        results["thrust_limited"] = closure["thrust_limited"]

    return results
