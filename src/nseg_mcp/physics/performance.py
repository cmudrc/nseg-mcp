"""High-level performance aggregation utilities.

Provides helpers for computing mission-level metrics from segment results.
"""

from __future__ import annotations

from typing import Any


def compute_block_fuel(segments: list[dict[str, Any]]) -> float:
    """Sum fuel burned across all segments (taxi-to-taxi)."""
    return float(sum(seg.get("fuel_burned_kg", 0.0) for seg in segments))


def compute_block_range_nm(segments: list[dict[str, Any]]) -> float:
    """Sum horizontal distance across airborne segments [nmi]."""
    airborne = {"takeoff", "climb", "cruise", "descent", "approach", "landing"}
    return float(sum(seg.get("distance_m", 0.0) for seg in segments if seg.get("segment_type") in airborne)) / 1852.0


def compute_block_time_hr(segments: list[dict[str, Any]]) -> float:
    """Sum time across all segments [hours]."""
    return float(sum(seg.get("time_s", 0.0) for seg in segments)) / 3600.0


def payload_range_point(
    oew_kg: float,
    max_fuel_kg: float,
    max_payload_kg: float,
    mtow_kg: float,
    range_nm: float,
    fuel_burned_kg: float,
) -> dict[str, float]:
    """Compute a single point on the payload-range diagram.

    Returns available payload after subtracting fuel and OEW from MTOW.
    """
    available_weight = mtow_kg - oew_kg - fuel_burned_kg
    actual_payload = min(max_payload_kg, max(0.0, available_weight))
    return {
        "range_nm": range_nm,
        "payload_kg": actual_payload,
        "fuel_kg": fuel_burned_kg,
        "total_weight_kg": oew_kg + actual_payload + fuel_burned_kg,
    }
