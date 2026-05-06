"""Define mission segments for a session."""

from __future__ import annotations

from typing import Any

from ..session_manager import session_manager

VALID_SEGMENT_TYPES = {"taxi", "takeoff", "climb", "cruise", "descent", "approach", "landing"}


def set_segments(payload: dict[str, Any]) -> dict[str, Any]:
    """Configure the ordered list of flight segments.

    Parameters
    ----------
    payload : dict
        ``session_id`` – mission session to update.
        ``segments`` – list of segment dicts, each containing:
            ``type`` – one of taxi/takeoff/climb/cruise/descent/approach/landing.
            ``start_altitude_m`` – segment start altitude [m].
            ``end_altitude_m`` – segment end altitude [m].
            ``mach`` – cruise/climb Mach number.
            ``distance_m`` – segment ground distance [m] (for cruise).
            ``duration_s`` – segment duration [s] (for taxi).
    """
    session_id = payload.get("session_id")
    if not session_id:
        return {"error": {"type": "ValidationError", "message": "session_id is required"}}

    raw_segments: list[dict[str, Any]] = payload.get("segments", [])
    if not raw_segments:
        return {"error": {"type": "ValidationError", "message": "segments list must not be empty"}}

    validated: list[dict[str, Any]] = []
    for i, seg in enumerate(raw_segments):
        seg_type = seg.get("type", "").lower()
        if seg_type not in VALID_SEGMENT_TYPES:
            return {
                "error": {
                    "type": "ValidationError",
                    "message": f"Segment {i}: type '{seg_type}' not in {sorted(VALID_SEGMENT_TYPES)}",
                }
            }
        validated.append(
            {
                "type": seg_type,
                "start_altitude_m": float(seg.get("start_altitude_m", 0)),
                "end_altitude_m": float(seg.get("end_altitude_m", 0)),
                "mach": float(seg.get("mach", 0)),
                "distance_m": float(seg.get("distance_m", 0)),
                "duration_s": float(seg.get("duration_s", 0)),
            }
        )

    session = session_manager.get(str(session_id))
    session.segments = validated

    return {"success": True, "segment_count": len(validated), "segments": validated}
