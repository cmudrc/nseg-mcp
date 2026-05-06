"""End-to-end test of the mission analysis pipeline."""

from nseg_mcp.tools.create_mission import close_mission, create_mission
from nseg_mcp.tools.get_results import get_results
from nseg_mcp.tools.run_mission import run_mission
from nseg_mcp.tools.set_segments import set_segments
from nseg_mcp.tools.set_vehicle import set_vehicle


def test_full_mission():
    result = create_mission({"name": "test_flight"})
    sid = result["session_id"]

    set_vehicle(
        {
            "session_id": sid,
            "weight_kg": 78000.0,
            "wing_area_m2": 130.0,
            "cd0": 0.02,
            "k": 0.04,
            "tsfc_1_per_s": 1.7e-5,
            "max_thrust_n": 120000.0,
        }
    )

    set_segments(
        {
            "session_id": sid,
            "segments": [
                {"type": "taxi", "duration_s": 300},
                {"type": "takeoff"},
                {"type": "climb", "start_altitude_m": 0, "end_altitude_m": 10668, "mach": 0.5},
                {
                    "type": "cruise",
                    "start_altitude_m": 10668,
                    "end_altitude_m": 10668,
                    "mach": 0.78,
                    "distance_m": 3000000,
                },
                {"type": "descent", "start_altitude_m": 10668, "end_altitude_m": 600, "mach": 0.5},
                {"type": "approach", "start_altitude_m": 600},
                {"type": "landing"},
            ],
        }
    )

    run_result = run_mission({"session_id": sid})
    assert run_result["success"] is True
    assert run_result["total_fuel_burned_kg"] > 0
    assert run_result["total_distance_m"] > 0
    assert len(run_result["segments"]) == 7

    get_result = get_results({"session_id": sid})
    assert get_result["success"] is True

    close_mission({"session_id": sid})
