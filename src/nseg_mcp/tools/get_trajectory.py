"""Return per-segment summaries from a completed NSEG run."""

from __future__ import annotations

from typing import Any

from ..session_manager import session_manager


def get_trajectory(payload: dict[str, Any]) -> dict[str, Any]:
    """Return per-segment summary data from the last NSEG run.

    NSEG provides idealized segment-by-segment summaries (start/end altitude,
    Mach, distance, fuel burned per segment) rather than continuous timeseries.

    Parameters
    ----------
    payload : dict
        ``session_id`` -- NSEG session with completed run_mission.
    """
    session_id = payload.get("session_id")
    if not session_id:
        return {"error": {"type": "ValidationError", "message": "session_id is required"}}

    session = session_manager.get(str(session_id))

    if session.results and session.results.get("segments"):
        return {
            "success": True,
            "session_id": session_id,
            "backend": "nseg",
            "trajectory": {
                "segments": session.results["segments"],
                "note": "NSEG backend provides per-segment data, not continuous timeseries.",
            },
        }

    return {"error": {"type": "RuntimeError", "message": "No segment data. Run run_mission first."}}
