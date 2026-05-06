"""Factory for the FastMCP server exposing NSEG mission analysis tools."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server import FastMCP

from . import tools

__all__ = ["build_server"]

LOGGER = logging.getLogger(__name__)


def _register_tools(server: FastMCP) -> None:
    """Attach NSEG tool implementations to a FastMCP instance."""

    @server.tool(
        name="create_mission",
        description="Create a new NSEG mission analysis session.",
        tags={"nseg", "mission", "session"},
    )
    def create_mission_tool(name: str = "unnamed_mission") -> dict[str, Any]:
        return tools.create_mission({"name": name})

    @server.tool(
        name="close_mission",
        description="Close an NSEG mission session and free resources.",
        tags={"nseg", "mission", "session"},
    )
    def close_mission_tool(session_id: str) -> dict[str, Any]:
        return tools.close_mission({"session_id": session_id})

    @server.tool(
        name="set_vehicle",
        description=(
            "Set vehicle parameters: weight_kg, wing_area_m2, cd0, k (induced drag factor), tsfc_1_per_s, max_thrust_n."
        ),
        tags={"nseg", "vehicle"},
    )
    def set_vehicle_tool(
        session_id: str,
        weight_kg: float,
        wing_area_m2: float,
        cd0: float,
        k: float,
        tsfc_1_per_s: float,
        max_thrust_n: float = 0.0,
        cl_max: float = 2.0,
    ) -> dict[str, Any]:
        return tools.set_vehicle(
            {
                "session_id": session_id,
                "weight_kg": weight_kg,
                "wing_area_m2": wing_area_m2,
                "cd0": cd0,
                "k": k,
                "tsfc_1_per_s": tsfc_1_per_s,
                "max_thrust_n": max_thrust_n,
                "cl_max": cl_max,
            }
        )

    @server.tool(
        name="set_segments",
        description=(
            "Define the ordered list of flight segments. Each segment has a type "
            "(taxi/takeoff/climb/cruise/descent/approach/landing) and parameters."
        ),
        tags={"nseg", "segments"},
    )
    def set_segments_tool(
        session_id: str,
        segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return tools.set_segments({"session_id": session_id, "segments": segments})

    @server.tool(
        name="configure_mission",
        description=(
            "Set mission profile: range_nmi, num_passengers, cruise_mach, cruise_altitude_ft."
        ),
        tags={"nseg", "configuration"},
    )
    def configure_mission_tool(
        session_id: str,
        range_nmi: float | None = None,
        num_passengers: int | None = None,
        cruise_mach: float | None = None,
        cruise_altitude_ft: float | None = None,
    ) -> dict[str, Any]:
        p: dict[str, Any] = {"session_id": session_id}
        if range_nmi is not None:
            p["range_nmi"] = range_nmi
        if num_passengers is not None:
            p["num_passengers"] = num_passengers
        if cruise_mach is not None:
            p["cruise_mach"] = cruise_mach
        if cruise_altitude_ft is not None:
            p["cruise_altitude_ft"] = cruise_altitude_ft
        return tools.configure_mission(p)

    @server.tool(
        name="run_mission",
        description="Execute NSEG segment-based mission analysis.",
        tags={"nseg", "execution"},
    )
    def run_mission_tool(session_id: str) -> dict[str, Any]:
        return tools.run_mission({"session_id": session_id})

    @server.tool(
        name="get_results",
        description="Retrieve results from the last NSEG mission run.",
        tags={"nseg", "results"},
    )
    def get_results_tool(session_id: str) -> dict[str, Any]:
        return tools.get_results({"session_id": session_id})

    @server.tool(
        name="get_trajectory",
        description="Return per-segment summaries from the last NSEG run.",
        tags={"nseg", "trajectory"},
    )
    def get_trajectory_tool(
        session_id: str,
        variables: list[str] | None = None,
    ) -> dict[str, Any]:
        p: dict[str, Any] = {"session_id": session_id}
        if variables:
            p["variables"] = variables
        return tools.get_trajectory(p)

    @server.tool(
        name="check_constraints",
        description=(
            "Evaluate pass/fail for user-defined constraints on NSEG mission results. "
            "Supports <=, >=, == operators on fuel_burned_kg, total_distance_nm, etc."
        ),
        tags={"nseg", "constraints"},
    )
    def check_constraints_tool(
        session_id: str,
        constraints: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return tools.check_constraints(
            {
                "session_id": session_id,
                "constraints": constraints,
            }
        )


def build_server() -> FastMCP:
    """Construct a FastMCP server with all NSEG tools registered."""
    server = FastMCP(
        name="nseg-mcp",
        instructions=(
            "NSEG MCP -- mission analysis using idealized flight segments. "
            "Each mission is decomposed into taxi/takeoff/climb/cruise/descent/approach/landing "
            "segments solved via Breguet range and energy methods. "
            "Create a session, set the vehicle, set segments (or configure_mission for defaults), "
            "then run_mission. Lower fidelity than Aviary trajectory-opt but much faster -- ideal "
            "for sweeps and point-performance trade studies."
        ),
    )
    _register_tools(server)
    LOGGER.debug("FastMCP NSEG server configured")
    return server
