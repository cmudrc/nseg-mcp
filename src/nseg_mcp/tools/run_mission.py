"""Execute the full NSEG segment-based mission analysis."""

from __future__ import annotations

import logging
from typing import Any

from ..physics.segments import (
    SEGMENT_DISPATCH,
    SegmentResult,
    thrust_required_top_of_climb,
)
from ..session_manager import session_manager

logger = logging.getLogger(__name__)


def _run_nseg(session: Any) -> dict[str, Any]:
    """Run all segments in order using the NSEG-style segment physics."""
    vehicle = session.vehicle
    if not vehicle:
        return {"error": {"type": "ValidationError", "message": "Vehicle data not set. Call set_vehicle first."}}
    if not session.segments:
        return {"error": {"type": "ValidationError", "message": "No segments defined. Call set_segments first."}}

    weight = vehicle["weight_kg"]
    cd0 = vehicle["cd0"]
    k = vehicle["k"]
    wing_area_m2 = vehicle["wing_area_m2"]
    tsfc = vehicle["tsfc_1_per_s"]

    segment_results: list[dict[str, Any]] = []
    total_fuel = 0.0
    total_distance = 0.0
    total_time = 0.0

    for seg_def in session.segments:
        seg_type = seg_def["type"]
        handler = SEGMENT_DISPATCH.get(seg_type)
        if handler is None:
            return {"error": {"type": "RuntimeError", "message": f"Unknown segment type: {seg_type}"}}

        kwargs: dict[str, Any] = {
            "weight_kg": weight,
            "cd0": cd0,
            "k": k,
            "wing_area_m2": wing_area_m2,
            "tsfc_1_per_s": tsfc,
            "start_altitude_m": seg_def.get("start_altitude_m", 0),
            "end_altitude_m": seg_def.get("end_altitude_m", 0),
            "altitude_m": seg_def.get("end_altitude_m", seg_def.get("start_altitude_m", 0)),
            "mach": seg_def.get("mach", 0),
            "distance_m": seg_def.get("distance_m", 0),
            "duration_s": seg_def.get("duration_s", 0),
        }

        result: SegmentResult = handler(**kwargs)
        segment_results.append(result.to_dict())
        weight = result.end_weight_kg
        total_fuel += result.fuel_burned_kg
        total_distance += result.distance_m
        total_time += result.time_s

    summary = {
        "success": True,
        "backend": "nseg",
        "initial_weight_kg": vehicle["weight_kg"],
        "final_weight_kg": weight,
        "total_fuel_burned_kg": total_fuel,
        "fuel_burned_kg": total_fuel,
        "total_distance_m": total_distance,
        "total_distance_nm": total_distance / 1852.0,
        "total_time_s": total_time,
        "total_time_hr": total_time / 3600.0,
        "fuel_fraction": total_fuel / vehicle["weight_kg"],
        "segments": segment_results,
    }

    thrust = _thrust_closure(session.segments, vehicle, cd0, k, wing_area_m2)
    if thrust is not None:
        summary["thrust_closure"] = thrust
        summary["thrust_limited"] = thrust["thrust_limited"]

    return summary


def _thrust_closure(
    segments: list[dict[str, Any]],
    vehicle: dict[str, Any],
    cd0: float,
    k: float,
    wing_area_m2: float,
) -> dict[str, Any] | None:
    """Top-of-climb thrust margin: does the engine actually close the mission?

    NSEG's segment integrators assume thrust is always available.  This adds a
    real availability check at the most binding point (top of climb), comparing
    the required thrust against the engine's installed thrust ``max_thrust_n``.
    A negative margin means the engine is too small – the mission does not close.
    """
    max_thrust = vehicle.get("max_thrust_n")
    if not max_thrust:
        return None

    cruise_seg = next((s for s in segments if s.get("type") == "cruise"), None)
    if cruise_seg is None:
        return None

    alt = cruise_seg.get("end_altitude_m", cruise_seg.get("start_altitude_m", 0)) or 0.0
    mach = cruise_seg.get("mach", 0.0) or 0.0
    req = thrust_required_top_of_climb(vehicle["weight_kg"], cd0, k, wing_area_m2, mach, alt)

    t_req = req["thrust_required_n"]
    if t_req != t_req:  # NaN guard
        return None

    margin = float(max_thrust) - t_req
    return {
        "criterion": "top_of_climb_residual_roc",
        "cruise_altitude_m": round(float(alt), 1),
        "cruise_mach": round(float(mach), 4),
        "cruise_drag_n": round(req["drag_n"], 2),
        "thrust_required_n": round(t_req, 2),
        "thrust_available_n": round(float(max_thrust), 2),
        "thrust_margin_n": round(margin, 2),
        "thrust_margin_frac": round(margin / float(max_thrust), 4),
        "thrust_limited": bool(margin < 0.0),
    }


def run_mission(payload: dict[str, Any]) -> dict[str, Any]:
    """Run NSEG mission analysis with the configured vehicle and segments.

    Parameters
    ----------
    payload : dict
        ``session_id`` -- mission session with vehicle and segments set.
    """
    session_id = payload.get("session_id")
    if not session_id:
        return {"error": {"type": "ValidationError", "message": "session_id is required"}}

    session = session_manager.get(str(session_id))
    summary = _run_nseg(session)
    session.results = summary
    return summary
