"""Shared-CPACS adapter for the NSEG MCP.

Reads vehicle data and aerodynamic/engine results from the CPACS XML,
runs NSEG segment-based mission analysis, and writes mission results
back into ``//vehicles/aircraft/model/analysisResults/mission``. Every
write is also recorded as an ``<update>`` under ``header/updates``.

Two mission profiles: the default climb-cruise-descent chain, and the ATA
Standard Mission Rules profile selected with ``mission_profile["rules"] ==
"ATA"`` (see :mod:`nseg_mcp.ata_mission`).
"""

from __future__ import annotations

import importlib.metadata
import logging
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree as ET

from nseg_mcp.ata_mission import run_ata_mission
from nseg_mcp.tools.create_mission import close_mission, create_mission
from nseg_mcp.tools.run_mission import run_mission
from nseg_mcp.tools.set_segments import set_segments
from nseg_mcp.tools.set_vehicle import set_vehicle

logger = logging.getLogger(__name__)

#: The aircraft's own stated take-off mass, relative to ``vehicles/aircraft/model``.
MTOM_PATH = "analyses/massBreakdown/designMasses/mTOM/mass"


def _float_or_none(el: ET.Element | None) -> float | None:
    """Read an element's text as a float, or None when it is absent or empty.

    Deliberately returns None rather than a default. Substituting a plausible
    number here is how a mission got flown at 78,000 kg on an aircraft that
    never stated a weight, with the constant then written into the shared CPACS
    where it was indistinguishable from a computed result.
    """
    if el is None or not el.text:
        return None
    return float(el.text)


def read_from_cpacs(
    cpacs_xml: str,
    mission_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract vehicle + aero + engine data from CPACS for NSEG analysis.

    Physical properties of the aircraft come back as ``None`` when the file
    does not state them; :func:`run_adapter` refuses to run in that case.
    Flight-condition values (cruise Mach, altitude, range) are different in
    kind: they describe the mission being asked for rather than the aircraft,
    so they keep documented defaults that the caller can override.

    The take-off weight is the one aircraft property with a second source:
    when the caller gives no ``weight_kg`` the file's own stated mTOM at
    ``analyses/massBreakdown/designMasses/mTOM/mass`` is used, and
    ``weight_source`` records which it was.
    """
    root = ET.fromstring(cpacs_xml)

    ref_area = _float_or_none(root.find(".//vehicles/aircraft/model/reference/area"))

    coeff = ".//vehicles/aircraft/model/analysisResults/aero/coefficients/"
    cd = _float_or_none(root.find(coeff + "CD"))
    cd0 = _float_or_none(root.find(coeff + "CD0"))
    cl = _float_or_none(root.find(coeff + "CL"))

    # Induced-drag factor from the polar CD = CD0 + k*CL^2. Needs all three.
    k: float | None = None
    if cd is not None and cd0 is not None and cl is not None and cl > 0.01:
        k = round((cd - cd0) / (cl * cl), 6)

    tsfc = _float_or_none(root.find(".//vehicles/engines/engine/analysis/mcpResults/TSFC_1_per_s"))
    max_thrust = _float_or_none(root.find(".//vehicles/engines/engine/analysis/mcpResults/Fn_N"))

    mp = mission_profile or {}

    # Take-off weight: the caller's value first, then the mass the file itself
    # states. Reading the aircraft's own mTOM is not a default; a number from
    # nowhere would be. Where it came from travels with the result.
    weight = mp.get("weight_kg")
    weight_source: str | None = None
    if weight is not None:
        weight_source = "mission_profile:weight_kg"
    else:
        weight = _float_or_none(root.find(".//vehicles/aircraft/model/" + MTOM_PATH))
        if weight is not None:
            weight_source = "cpacs:" + MTOM_PATH

    rules = mp.get("rules")
    if isinstance(rules, str):
        rules = rules.strip().upper() or None

    return {
        "ref_area_m2": ref_area,
        "cd": cd,
        "cd0": cd0,
        "k": k,
        "tsfc_1_per_s": tsfc,
        "max_thrust_n": max_thrust,
        "weight_kg": weight,
        "weight_source": weight_source,
        # Mission request, not aircraft property -- defaults are fine here.
        "cruise_mach": mp.get("cruise_mach", 0.78),
        "cruise_altitude_m": mp.get("cruise_altitude_m", 10668.0),
        "range_m": mp.get("range_m", 3_000_000.0),
        "segments": mp.get("segments"),
        # Profile selection and the inputs only the ATA profile reads. None
        # means "not given"; the ATA runner decides which of these it needs
        # and which it may default as mission conditions.
        "rules": rules,
        "diversion_altitude_ft": mp.get("diversion_altitude_ft"),
        "taxi_fuel_flow_kg_s": mp.get("taxi_fuel_flow_kg_s"),
        "passengers": mp.get("passengers"),
        "payload_kg": mp.get("payload_kg"),
        "climb_mach": mp.get("climb_mach"),
        "descent_mach": mp.get("descent_mach"),
        "climb_roc_m_s": mp.get("climb_roc_m_s"),
    }


#: Values NSEG cannot invent, with where each one has to come from.
_REQUIRED: tuple[tuple[str, str], ...] = (
    ("ref_area_m2", "the CPACS reference area at //vehicles/aircraft/model/reference/area"),
    ("cd0", "the aero stage -- run SU2 first, or supply a drag polar"),
    ("k", "the aero stage -- needs CD, CD0 and CL to fit CD = CD0 + k*CL^2"),
    ("tsfc_1_per_s", "the propulsion stage -- run pyCycle first"),
    ("max_thrust_n", "the propulsion stage -- run pyCycle first"),
    (
        "weight_kg",
        "the caller -- pass weight_kg in the mission profile "
        "(the orchestrator's --weight, or --oew/--payload in the "
        "cruise-match harness) -- or the file itself, at "
        "//vehicles/aircraft/model/analyses/massBreakdown/designMasses/mTOM/mass",
    ),
)


def _check_required(inputs: dict[str, Any]) -> dict[str, Any] | None:
    """Return a structured error naming everything the run is missing."""
    missing = [(f, src) for f, src in _REQUIRED if inputs.get(f) is None]
    if not missing:
        return None
    return {
        "type": "missing_input",
        "message": ("Cannot fly a mission: " + ", ".join(f for f, _ in missing) + " not available."),
        "details": (
            "These are properties of the aircraft and its mission. They are "
            "not defaulted, because a substituted value would be written into "
            "the shared CPACS and read downstream as a real result. Supply "
            "each one:\n" + "\n".join(f"  - {f}: from {src}" for f, src in missing)
        ),
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
    """Write NSEG results into ``//vehicles/aircraft/model/analysisResults/mission``.

    Also appends a provenance entry under ``header/updates`` saying what was
    written, by which version of nseg-mcp, and when.
    """
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
    if results.get("weight_source"):
        ET.SubElement(m_el, "weightSource").text = str(results["weight_source"])
    if results.get("payload_kg") is not None:
        ET.SubElement(m_el, "payloadKg").text = str(results["payload_kg"])
        ET.SubElement(m_el, "payloadSource").text = str(results.get("payload_source", ""))

    if results.get("rules") == "ATA":
        _write_ata_results(m_el, results)

    _write_segments(ET.SubElement(m_el, "segments"), results.get("segments", []))
    if results.get("reserve_segments"):
        _write_segments(ET.SubElement(m_el, "reserveSegments"), results["reserve_segments"])

    _append_header_update(root, results)

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _write_segments(parent: ET.Element, segments: list[dict[str, Any]]) -> None:
    for seg in segments:
        seg_el = ET.SubElement(parent, "segment")
        ET.SubElement(seg_el, "type").text = seg.get("segment_type", "unknown")
        if seg.get("name"):
            ET.SubElement(seg_el, "name").text = str(seg["name"])
        ET.SubElement(seg_el, "fuelBurnedKg").text = str(seg.get("fuel_burned_kg", 0.0))
        ET.SubElement(seg_el, "distanceM").text = str(seg.get("distance_m", 0.0))
        ET.SubElement(seg_el, "timeS").text = str(seg.get("time_s", 0.0))


#: ATA result keys written under the mission node, in this order, with their tags.
_ATA_SCALARS: tuple[tuple[str, str], ...] = (
    ("trip_fuel_kg", "tripFuelKg"),
    ("block_fuel_kg", "blockFuelKg"),
    ("fuel_load_kg", "fuelLoadKg"),
    ("taxi_out_kg", "taxiOutKg"),
    ("still_air_range_m", "stillAirRangeM"),
    ("still_air_range_nm", "stillAirRangeNm"),
    ("flight_time_s", "flightTimeS"),
    ("cruise_distance_m", "cruiseDistanceM"),
    ("cruise_time_s", "cruiseTimeS"),
    ("diversion_altitude_m", "diversionAltitudeM"),
    ("hold_speed_m_s", "holdSpeedMS"),
)

#: The reserve breakdown, written under ``mission/reserves``.
_ATA_RESERVES: tuple[tuple[str, str], ...] = (
    ("reserve_five_percent_kg", "reserveFivePercentKg"),
    ("missed_approach_kg", "missedApproachKg"),
    ("diversion_climb_kg", "diversionClimbKg"),
    ("diversion_cruise_kg", "diversionCruiseKg"),
    ("diversion_descent_kg", "diversionDescentKg"),
    ("diversion_approach_landing_kg", "diversionApproachLandingKg"),
    ("hold_kg", "holdKg"),
    ("taxi_in_kg", "taxiInKg"),
    ("reserve_total_kg", "reserveTotalKg"),
)


def _write_ata_results(m_el: ET.Element, results: dict[str, Any]) -> None:
    """The ATA accounting, with the same honesty flags as the results dict.

    A part that could not be computed (today: taxi fuel without an idle fuel
    flow) is simply absent here and named under ``gaps``, and
    ``blockFuelComplete`` says whether the block fuel covers every leg.
    """
    ET.SubElement(m_el, "rules").text = "ATA"
    for key, tag in _ATA_SCALARS:
        if results.get(key) is not None:
            ET.SubElement(m_el, tag).text = str(results[key])
    ET.SubElement(m_el, "blockFuelComplete").text = str(bool(results.get("block_fuel_complete", False))).lower()
    reserves_el = ET.SubElement(m_el, "reserves")
    for key, tag in _ATA_RESERVES:
        if results.get(key) is not None:
            ET.SubElement(reserves_el, tag).text = str(results[key])
    gaps_el = ET.SubElement(m_el, "gaps")
    for gap in results.get("gaps") or []:
        ET.SubElement(gaps_el, "gap").text = str(gap)
    ET.SubElement(m_el, "rulesNote").text = str(results.get("rules_note", ""))


def _package_version() -> str:
    try:
        return importlib.metadata.version("nseg-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _modification_sentence(results: dict[str, Any]) -> str:
    profile = "ATA Standard Mission Rules" if results.get("rules") == "ATA" else "climb-cruise-descent"
    fuel = results.get("total_fuel_burned_kg") or results.get("fuel_burned_kg", 0.0)
    tail = "" if results.get("block_fuel_complete", True) else ", block fuel incomplete, see gaps"
    return (
        f"nseg-mcp wrote {profile} mission results to vehicles/aircraft/model/analysisResults/mission "
        f"(block fuel {float(fuel):.1f} kg over {float(results.get('total_distance_nm', 0.0)):.0f} nm{tail})."
    )


def _append_header_update(root: ET.Element, results: dict[str, Any]) -> None:
    """Append one provenance entry under ``header/updates``.

    This is the CPACS-native record of who wrote what and when, the
    convention DLR pointed us to; the other servers follow the same one.
    ``updates`` is created directly after ``cpacsVersion`` when the header
    has none, so the header's schema order stays valid. Nothing already in
    the header is removed or moved. The entry's ``version`` is a running
    count of update entries, and ``cpacsVersion`` copies the header's.
    """
    header = root.find("header")
    if header is None:
        header = ET.Element("header")
        root.insert(0, header)

    updates = header.find("updates")
    if updates is None:
        updates = ET.Element("updates")
        anchor = header.find("cpacsVersion")
        if anchor is not None:
            header.insert(list(header).index(anchor) + 1, updates)
        else:
            header.append(updates)

    cpacs_version = (header.findtext("cpacsVersion") or header.findtext("version") or "").strip()
    count = len(updates.findall("update"))

    update = ET.SubElement(updates, "update")
    ET.SubElement(update, "modification").text = _modification_sentence(results)
    ET.SubElement(update, "creator").text = f"nseg-mcp {_package_version()}"
    ET.SubElement(update, "timestamp").text = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    ET.SubElement(update, "version").text = str(count + 1)
    ET.SubElement(update, "cpacsVersion").text = cpacs_version


def run_adapter(
    cpacs_xml: str,
    mission_profile: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Full read -> NSEG run -> write cycle for the Mission domain.

    Returns (updated_cpacs_xml, summary_dict).
    """
    inputs = read_from_cpacs(cpacs_xml, mission_profile)

    if inputs["rules"] not in (None, "ATA"):
        return cpacs_xml, {
            "success": False,
            "error": {
                "type": "invalid_input",
                "message": f"Unknown mission rules {inputs['rules']!r}.",
                "details": (
                    "Omit rules for the climb-cruise-descent profile with a flat reserve, "
                    "or pass rules == 'ATA' for the ATA Standard Mission Rules profile."
                ),
            },
            "solver": "nseg",
        }

    # Refuse to invent the aircraft. Everything in _REQUIRED used to carry a
    # hardcoded fallback, so a mission would run to completion on a file that
    # described no weight, no engine and no aerodynamics at all.
    missing = _check_required(inputs)
    if missing is not None:
        return cpacs_xml, {
            "success": False,
            "error": missing,
            "solver": "nseg",
        }

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

    if inputs["rules"] == "ATA":
        results = run_ata_mission(inputs)
    else:
        results = _run_with_nseg(inputs)
    results["weight_source"] = inputs["weight_source"]

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

    for segment in list(results.get("segments") or []) + list(results.get("reserve_segments") or []):
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
