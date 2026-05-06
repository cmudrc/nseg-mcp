"""Segment-level equations of motion for mission analysis.

Modelled after the NSEG segmented-mission approach: each flight phase
(taxi, takeoff, climb, cruise, descent, approach, landing) is computed
with simplified energy-based or Breguet-style equations.

All SI units unless noted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .atmosphere import G0, dynamic_pressure, mach_to_tas


@dataclass(frozen=True, slots=True)
class SegmentResult:
    """Output from a single segment analysis."""

    segment_type: str
    fuel_burned_kg: float
    distance_m: float
    time_s: float
    start_weight_kg: float
    end_weight_kg: float
    start_altitude_m: float
    end_altitude_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_type": self.segment_type,
            "fuel_burned_kg": self.fuel_burned_kg,
            "distance_m": self.distance_m,
            "time_s": self.time_s,
            "start_weight_kg": self.start_weight_kg,
            "end_weight_kg": self.end_weight_kg,
            "start_altitude_m": self.start_altitude_m,
            "end_altitude_m": self.end_altitude_m,
        }


def _drag(weight_kg: float, cd0: float, k: float, wing_area_m2: float, mach: float, altitude_m: float) -> float:
    """Compute total drag [N] using the parabolic polar CD = cd0 + k*CL^2."""
    q = dynamic_pressure(mach, altitude_m)
    W = weight_kg * G0
    if q * wing_area_m2 < 1e-6:
        return cd0 * 0.5 * 1.225 * 100.0 * 100.0 * wing_area_m2
    CL = W / (q * wing_area_m2)
    CD = cd0 + k * CL * CL
    return CD * q * wing_area_m2


def _lift_to_drag(weight_kg: float, cd0: float, k: float, wing_area_m2: float, mach: float, altitude_m: float) -> float:
    q = dynamic_pressure(mach, altitude_m)
    W = weight_kg * G0
    if q * wing_area_m2 < 1e-6:
        return 10.0
    CL = W / (q * wing_area_m2)
    CD = cd0 + k * CL * CL
    if CD < 1e-12:
        return 10.0
    return CL / CD


# ─── Individual segment solvers ─────────────────────────────────────────────


def taxi_segment(
    weight_kg: float,
    duration_s: float,
    tsfc_1_per_s: float,
    **_: Any,
) -> SegmentResult:
    """Ground taxi: constant fuel flow for a fixed duration.

    Fuel flow approximated as 7% of max thrust * TSFC (NSEG convention).
    """
    fuel_flow_kg_s = 0.07 * weight_kg * G0 * tsfc_1_per_s
    fuel = fuel_flow_kg_s * duration_s
    fuel = min(fuel, weight_kg * 0.1)
    return SegmentResult(
        segment_type="taxi",
        fuel_burned_kg=fuel,
        distance_m=0.0,
        time_s=duration_s,
        start_weight_kg=weight_kg,
        end_weight_kg=weight_kg - fuel,
        start_altitude_m=0.0,
        end_altitude_m=0.0,
    )


def takeoff_segment(
    weight_kg: float,
    tsfc_1_per_s: float,
    **_: Any,
) -> SegmentResult:
    """Simplified takeoff: assume 60 s at full thrust covering ~2 km."""
    duration_s = 60.0
    distance_m = 2000.0
    fuel = weight_kg * G0 * tsfc_1_per_s * duration_s * 0.5
    fuel = min(fuel, weight_kg * 0.05)
    return SegmentResult(
        segment_type="takeoff",
        fuel_burned_kg=fuel,
        distance_m=distance_m,
        time_s=duration_s,
        start_weight_kg=weight_kg,
        end_weight_kg=weight_kg - fuel,
        start_altitude_m=0.0,
        end_altitude_m=0.0,
    )


def climb_segment(
    weight_kg: float,
    start_altitude_m: float,
    end_altitude_m: float,
    mach: float,
    cd0: float,
    k: float,
    wing_area_m2: float,
    tsfc_1_per_s: float,
    **_: Any,
) -> SegmentResult:
    """Climb from start_altitude to end_altitude at constant Mach.

    Uses an energy-method approach: integrate altitude gain in discrete steps,
    computing drag and fuel burn at each step.  Rate of climb derived from
    excess specific power (thrust – drag).
    """
    N_STEPS = 50
    dh = (end_altitude_m - start_altitude_m) / N_STEPS
    W = weight_kg
    total_fuel = 0.0
    total_time = 0.0
    total_dist = 0.0

    for i in range(N_STEPS):
        h = start_altitude_m + i * dh
        V = mach_to_tas(mach, h)
        if V < 1.0:
            V = 1.0
        D = _drag(W, cd0, k, wing_area_m2, mach, h)
        T_required = D + W * G0 * (dh / max(V, 1.0))
        T_required = max(T_required, D * 1.05)

        dt = abs(dh) / max(V * 0.1, 1.0)
        sin_gamma = dh / (V * dt) if dt > 0 else 0.0
        sin_gamma = max(-0.5, min(0.5, sin_gamma))
        cos_gamma = math.sqrt(1.0 - sin_gamma * sin_gamma)

        ROC = V * sin_gamma if abs(sin_gamma) > 1e-9 else abs(dh) / max(dt, 0.01)
        if abs(ROC) < 0.01:
            ROC = 1.0
        dt = abs(dh) / abs(ROC)

        fuel_step = T_required * tsfc_1_per_s * dt
        fuel_step = min(fuel_step, W * 0.01)
        total_fuel += fuel_step
        W -= fuel_step
        total_time += dt
        total_dist += V * cos_gamma * dt

    return SegmentResult(
        segment_type="climb",
        fuel_burned_kg=total_fuel,
        distance_m=total_dist,
        time_s=total_time,
        start_weight_kg=weight_kg,
        end_weight_kg=W,
        start_altitude_m=start_altitude_m,
        end_altitude_m=end_altitude_m,
    )


def cruise_segment(
    weight_kg: float,
    altitude_m: float,
    mach: float,
    distance_m: float,
    cd0: float,
    k: float,
    wing_area_m2: float,
    tsfc_1_per_s: float,
    **_: Any,
) -> SegmentResult:
    """Cruise at constant altitude and Mach using the Breguet range equation.

    Fuel burn computed via:
        W_end = W_start * exp(-R * TSFC / (V * L/D))
    """
    V = mach_to_tas(mach, altitude_m)
    L_over_D = _lift_to_drag(weight_kg, cd0, k, wing_area_m2, mach, altitude_m)

    if V < 1.0 or L_over_D < 0.1:
        return SegmentResult(
            segment_type="cruise",
            fuel_burned_kg=0.0,
            distance_m=distance_m,
            time_s=distance_m / max(V, 1.0),
            start_weight_kg=weight_kg,
            end_weight_kg=weight_kg,
            start_altitude_m=altitude_m,
            end_altitude_m=altitude_m,
        )

    exponent = -distance_m * tsfc_1_per_s / (V * L_over_D)
    exponent = max(exponent, -2.0)
    W_end = weight_kg * math.exp(exponent)
    fuel = weight_kg - W_end
    time_s = distance_m / V

    return SegmentResult(
        segment_type="cruise",
        fuel_burned_kg=fuel,
        distance_m=distance_m,
        time_s=time_s,
        start_weight_kg=weight_kg,
        end_weight_kg=W_end,
        start_altitude_m=altitude_m,
        end_altitude_m=altitude_m,
    )


def descent_segment(
    weight_kg: float,
    start_altitude_m: float,
    end_altitude_m: float,
    mach: float,
    cd0: float,
    k: float,
    wing_area_m2: float,
    tsfc_1_per_s: float,
    **_: Any,
) -> SegmentResult:
    """Descent: idle thrust, gravity-assisted glide.

    Fuel burn is low (~10% of climb equivalent) since engines are at idle.
    """
    N_STEPS = 20
    dh = (end_altitude_m - start_altitude_m) / N_STEPS
    W = weight_kg
    total_fuel = 0.0
    total_time = 0.0
    total_dist = 0.0

    for i in range(N_STEPS):
        h = start_altitude_m + i * dh
        V = mach_to_tas(mach, h)
        if V < 1.0:
            V = 50.0
        D = _drag(W, cd0, k, wing_area_m2, mach, h)
        # Idle thrust ≈ 10% of drag
        T_idle = 0.1 * D
        ROD = abs(dh) / (abs(dh) / max(V * 0.05, 1.0)) if abs(dh) > 0.01 else 1.0
        dt = abs(dh) / max(ROD, 0.5)
        fuel_step = T_idle * tsfc_1_per_s * dt
        fuel_step = min(fuel_step, W * 0.005)
        total_fuel += fuel_step
        W -= fuel_step
        total_time += dt
        total_dist += V * dt

    return SegmentResult(
        segment_type="descent",
        fuel_burned_kg=total_fuel,
        distance_m=total_dist,
        time_s=total_time,
        start_weight_kg=weight_kg,
        end_weight_kg=W,
        start_altitude_m=start_altitude_m,
        end_altitude_m=end_altitude_m,
    )


def approach_segment(
    weight_kg: float,
    start_altitude_m: float,
    tsfc_1_per_s: float,
    **_: Any,
) -> SegmentResult:
    """Approach: slow descent from pattern altitude to runway."""
    duration_s = 180.0
    distance_m = 10000.0
    fuel = weight_kg * G0 * tsfc_1_per_s * duration_s * 0.3
    fuel = min(fuel, weight_kg * 0.02)
    return SegmentResult(
        segment_type="approach",
        fuel_burned_kg=fuel,
        distance_m=distance_m,
        time_s=duration_s,
        start_weight_kg=weight_kg,
        end_weight_kg=weight_kg - fuel,
        start_altitude_m=start_altitude_m,
        end_altitude_m=0.0,
    )


def landing_segment(
    weight_kg: float,
    tsfc_1_per_s: float,
    **_: Any,
) -> SegmentResult:
    """Landing: touchdown and deceleration."""
    duration_s = 30.0
    fuel = weight_kg * G0 * tsfc_1_per_s * duration_s * 0.15
    fuel = min(fuel, weight_kg * 0.01)
    return SegmentResult(
        segment_type="landing",
        fuel_burned_kg=fuel,
        distance_m=1500.0,
        time_s=duration_s,
        start_weight_kg=weight_kg,
        end_weight_kg=weight_kg - fuel,
        start_altitude_m=0.0,
        end_altitude_m=0.0,
    )


SEGMENT_DISPATCH: dict[str, Any] = {
    "taxi": taxi_segment,
    "takeoff": takeoff_segment,
    "climb": climb_segment,
    "cruise": cruise_segment,
    "descent": descent_segment,
    "approach": approach_segment,
    "landing": landing_segment,
}
