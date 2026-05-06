"""Set vehicle parameters for a mission session."""

from __future__ import annotations

from typing import Any

from ..session_manager import session_manager


def set_vehicle(payload: dict[str, Any]) -> dict[str, Any]:
    """Set vehicle aerodynamic and propulsion data.

    Parameters
    ----------
    payload : dict
        ``session_id`` – mission session to update.
        ``weight_kg`` – takeoff gross weight [kg].
        ``wing_area_m2`` – reference wing area [m^2].
        ``cd0`` – zero-lift drag coefficient.
        ``k`` – induced drag factor (CL^2 coefficient).  CD = cd0 + k * CL^2.
        ``tsfc_1_per_s`` – thrust-specific fuel consumption [1/s].
        ``max_thrust_n`` – maximum available thrust [N] (sea-level static).
        ``cl_max`` – maximum lift coefficient (optional, default 2.0).
    """
    session_id = payload.get("session_id")
    if not session_id:
        return {"error": {"type": "ValidationError", "message": "session_id is required"}}

    session = session_manager.get(str(session_id))

    vehicle: dict[str, Any] = {}
    for key in ("weight_kg", "wing_area_m2", "cd0", "k", "tsfc_1_per_s", "max_thrust_n", "cl_max"):
        if key in payload:
            vehicle[key] = float(payload[key])

    vehicle.setdefault("cl_max", 2.0)
    session.vehicle = vehicle

    return {"success": True, "vehicle": vehicle}
