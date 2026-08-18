"""Shared-CPACS adapter for the NSEG MCP.

Reads vehicle data and aerodynamic/engine results from the CPACS XML,
runs NSEG segment-based mission analysis, and writes mission results
back into ``//vehicles/aircraft/model/analysisResults/mission``.
"""

from __future__ import annotations

import logging
from typing import Any
from xml.etree import ElementTree as ET

from nseg_mcp.tools.create_mission import close_mission, create_mission
from nseg_mcp.tools.run_mission import run_mission
from nseg_mcp.tools.set_segments import set_segments
from nseg_mcp.tools.set_vehicle import set_vehicle

logger = logging.getLogger(__name__)


def read_from_cpacs(
    cpacs_xml: str,
    mission_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract vehicle + aero + engine data from CPACS for NSEG analysis."""
    root = ET.fromstring(cpacs_xml)

    ref_area_el = root.find(".//vehicles/aircraft/model/reference/area")
    ref_area = float(ref_area_el.text) if ref_area_el is not None and ref_area_el.text else 122.4

    cd = 0.025
    aero_cd = root.find(".//vehicles/aircraft/model/analysisResults/aero/coefficients/CD")
    if aero_cd is not None and aero_cd.text:
        cd = float(aero_cd.text)

    cd0_el = root.find(".//vehicles/aircraft/model/analysisResults/aero/coefficients/CD0")
    cd0 = float(cd0_el.text) if cd0_el is not None and cd0_el.text else 0.020

    cl_el = root.find(".//vehicles/aircraft/model/analysisResults/aero/coefficients/CL")
    cl = float(cl_el.text) if cl_el is not None and cl_el.text else 0.5
    k = (cd - cd0) / (cl * cl) if cl > 0.01 else 0.04

    tsfc_el = root.find(".//vehicles/engines/engine/analysis/mcpResults/TSFC_1_per_s")
    tsfc = float(tsfc_el.text) if tsfc_el is not None and tsfc_el.text else 1.7e-5

    fn_el = root.find(".//vehicles/engines/engine/analysis/mcpResults/Fn_N")
    max_thrust = float(fn_el.text) if fn_el is not None and fn_el.text else 120000.0

    mp = mission_profile or {}

    return {
        "ref_area_m2": ref_area,
        "cd0": cd0,
        "k": round(k, 6),
        "tsfc_1_per_s": tsfc,
        "max_thrust_n": max_thrust,
        "weight_kg": mp.get("weight_kg", 78000.0),
        "cruise_mach": mp.get("cruise_mach", 0.78),
        "cruise_altitude_m": mp.get("cruise_altitude_m", 10668.0),
        "range_m": mp.get("range_m", 3_000_000.0),
        "segments": mp.get("segments"),
    }


def _build_default_segments(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a standard taxi-takeoff-climb-cruise-descent-approach-landing profile."""
    alt = inputs["cruise_altitude_m"]
    mach = inputs["cruise_mach"]
    return [
        {"type": "taxi", "duration_s": 300},
        {"type": "takeoff"},
        {"type": "climb", "start_altitude_m": 0, "end_altitude_m": alt, "mach": min(mach, 0.6)},
        {
            "type": "cruise",
            "start_altitude_m": alt,
            "end_altitude_m": alt,
            "mach": mach,
            "distance_m": inputs["range_m"],
        },
        {"type": "descent", "start_altitude_m": alt, "end_altitude_m": 600, "mach": min(mach, 0.5)},
        {"type": "approach", "start_altitude_m": 600},
        {"type": "landing"},
    ]


def write_to_cpacs(cpacs_xml: str, results: dict[str, Any]) -> str:
    """Write NSEG results into ``//vehicles/aircraft/model/analysisResults/mission``."""
    root = ET.fromstring(cpacs_xml)

    model = root.find(".//vehicles/aircraft/model")
    if model is None:
        model = _ensure_path(root, "vehicles/aircraft/model")

    ar = model.find("analysisResults")
    if ar is None:
        ar = ET.SubElement(model, "analysisResults")

    existing = ar.find("mission")
    if existing is not None:
        ar.remove(existing)

    m_el = ET.SubElement(ar, "mission")
    ET.SubElement(m_el, "backend").text = "nseg"
    ET.SubElement(m_el, "success").text = str(results.get("success", False)).lower()

    fuel = results.get("total_fuel_burned_kg") or results.get("fuel_burned_kg", 0.0)
    ET.SubElement(m_el, "totalFuelBurnedKg").text = str(fuel)
    ET.SubElement(m_el, "initialWeightKg").text = str(results.get("initial_weight_kg", 0.0))
    ET.SubElement(m_el, "finalWeightKg").text = str(results.get("final_weight_kg", 0.0))
    ET.SubElement(m_el, "totalDistanceM").text = str(results.get("total_distance_m", 0.0))
    ET.SubElement(m_el, "totalDistanceNm").text = str(results.get("total_distance_nm", 0.0))
    ET.SubElement(m_el, "totalTimeS").text = str(results.get("total_time_s", 0.0))
    ET.SubElement(m_el, "totalTimeHr").text = str(results.get("total_time_hr", 0.0))
    ET.SubElement(m_el, "fuelFraction").text = str(results.get("fuel_fraction", 0.0))

    segs_el = ET.SubElement(m_el, "segments")
    for seg in results.get("segments", []):
        seg_el = ET.SubElement(segs_el, "segment")
        ET.SubElement(seg_el, "type").text = seg.get("segment_type", "unknown")
        ET.SubElement(seg_el, "fuelBurnedKg").text = str(seg.get("fuel_burned_kg", 0.0))
        ET.SubElement(seg_el, "distanceM").text = str(seg.get("distance_m", 0.0))
        ET.SubElement(seg_el, "timeS").text = str(seg.get("time_s", 0.0))

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def run_adapter(
    cpacs_xml: str,
    mission_profile: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Full read -> NSEG run -> write cycle for the Mission domain.

    Returns (updated_cpacs_xml, summary_dict).
    """
    inputs = read_from_cpacs(cpacs_xml, mission_profile)

    # Refuse to fly a mission on an impossible engine. A negative available
    # thrust reaching this point produced a block fuel of -2.86e9 kg on a
    # partner's machine, because every stage trusted the stage before it.
    max_thrust = inputs.get("max_thrust_n")
    if max_thrust is not None and float(max_thrust) <= 0.0:
        return cpacs_xml, {
            "success": False,
            "error": {
                "type": "unphysical_input",
                "message": (
                    f"Refusing to run a mission with a non-positive available thrust ({float(max_thrust):.6g} N)."
                ),
                "details": (
                    "This value comes from the propulsion stage. Check that the "
                    "engine sized successfully before running the mission."
                ),
            },
            "solver": "nseg",
        }

    results = _run_with_nseg(inputs)

    unphysical = _check_mission_results(results)
    if unphysical is not None:
        results["success"] = False
        results["error"] = unphysical
        return cpacs_xml, results

    if results.get("success"):
        updated_xml = write_to_cpacs(cpacs_xml, results)
    else:
        updated_xml = cpacs_xml

    return updated_xml, results


def _check_mission_results(results: dict[str, Any]) -> dict[str, Any] | None:
    """Return a structured error if the mission produced impossible numbers.

    An aircraft cannot burn a negative quantity of fuel. This is an
    impossibility check, not an accuracy check: it says nothing about whether a
    positive fuel burn is correct.
    """
    fuel = results.get("total_fuel_burned_kg")
    if fuel is None:
        fuel = results.get("fuel_burned_kg")
    if fuel is not None and float(fuel) < 0.0:
        return {
            "type": "unphysical_result",
            "message": (f"Mission returned a negative block fuel ({float(fuel):.6g} kg)."),
            "details": (
                "Negative fuel burn means a segment was flown with negative "
                "thrust or negative TSFC. Check the propulsion stage before "
                "the mission stage. The result was not written to CPACS."
            ),
        }

    for segment in results.get("segments", []) or []:
        seg_fuel = segment.get("fuel_burned_kg")
        if seg_fuel is not None and float(seg_fuel) < 0.0:
            return {
                "type": "unphysical_result",
                "message": (
                    f"Mission segment '{segment.get('name', '?')}' burned negative fuel ({float(seg_fuel):.6g} kg)."
                ),
                "details": ("Check the thrust available in the propulsion stage. The result was not written to CPACS."),
            }
    return None


def _run_with_nseg(inputs: dict[str, Any]) -> dict[str, Any]:
    """NSEG execution using the MCP tool functions."""
    session = create_mission({"name": "cpacs_mission"})
    sid = session["session_id"]

    try:
        set_vehicle(
            {
                "session_id": sid,
                "weight_kg": inputs["weight_kg"],
                "wing_area_m2": inputs["ref_area_m2"],
                "cd0": inputs["cd0"],
                "k": inputs["k"],
                "tsfc_1_per_s": inputs["tsfc_1_per_s"],
                "max_thrust_n": inputs["max_thrust_n"],
            }
        )

        segments = inputs.get("segments") or _build_default_segments(inputs)
        set_segments({"session_id": sid, "segments": segments})

        results = run_mission({"session_id": sid})
    finally:
        close_mission({"session_id": sid})

    return results


def _ensure_path(root: ET.Element, path: str) -> ET.Element:
    current = root
    for part in path.split("/"):
        child = current.find(part)
        if child is None:
            child = ET.SubElement(current, part)
        current = child
    return current
