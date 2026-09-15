"""Segment-level equations of motion for mission analysis.

Modelled after the NSEG segmented-mission approach: each flight phase
(taxi, takeoff, climb, cruise, descent, approach, landing, hold) is computed
with simplified energy-based or Breguet-style equations.

All SI units unless noted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .atmosphere import G0, dynamic_pressure, isa, mach_to_tas


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


def thrust_required_top_of_climb(
    weight_kg: float,
    cd0: float,
    k: float,
    wing_area_m2: float,
    mach: float,
    altitude_m: float,
    roc_req_m_s: float = 1.524,
) -> dict[str, float]:
    """Thrust required to sustain the residual top-of-climb rate at altitude.

    Top-of-climb is the classic engine-sizing driver: at the cruise ceiling the
    engine must still deliver the cruise drag *plus* enough excess thrust for a
    residual rate of climb (FAR/industry convention is ~300 ft/min ≈ 1.524 m/s).

        T_req = D + W * (ROC / V)

    where ``D`` is the steady-level drag at the cruise lift coefficient and
    ``V`` is the true airspeed.  Returns the drag, true airspeed and required
    thrust in SI units.  This is a real physical relation – no fudge factors.
    """
    q = dynamic_pressure(mach, altitude_m)
    W = weight_kg * G0
    if q * wing_area_m2 < 1e-6 or mach <= 0.0:
        return {"drag_n": float("nan"), "tas_m_s": float("nan"), "thrust_required_n": float("nan")}
    CL = W / (q * wing_area_m2)
    CD = cd0 + k * CL * CL
    drag_n = CD * q * wing_area_m2
    V = mach_to_tas(mach, altitude_m)
    thrust_required_n = drag_n + W * (roc_req_m_s / max(V, 1.0))
    return {
        "drag_n": drag_n,
        "tas_m_s": V,
        "thrust_required_n": thrust_required_n,
    }


# ─── Individual segment solvers ─────────────────────────────────────────────


def taxi_segment(
    weight_kg: float,
    duration_s: float,
    tsfc_1_per_s: float,
    fuel_flow_kg_s: float | None = None,
    **_: Any,
) -> SegmentResult:
    """Ground taxi: constant fuel flow for a fixed duration.

    With ``fuel_flow_kg_s`` given (an idle fuel flow, from the propulsion
    stage or stated by the caller) the fuel is simply flow * duration. The
    ATA profile in :mod:`nseg_mcp.ata_mission` only flies taxi legs this way
    and lists the leg as a gap when no idle fuel flow is available.

    Without it the NSEG convention is used: fuel flow approximated as 7% of
    the weight, as thrust, times TSFC, capped at 10% of the weight. The
    adapter's default profile keeps this behaviour.
    """
    if fuel_flow_kg_s is not None:
        fuel = float(fuel_flow_kg_s) * duration_s
    else:
        fuel = 0.07 * weight_kg * G0 * tsfc_1_per_s * duration_s
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
    roc_m_s: float = 7.62,
    **_: Any,
) -> SegmentResult:
    """Climb from start_altitude to end_altitude at constant Mach.

    Integrates the climb in altitude steps. At each step the thrust required
    is drag plus the component of weight along the flight path:

        T = D + W*g0*sin(gamma),   sin(gamma) = ROC / V

    and fuel is T * tsfc * dt with dt = dh / ROC. The rate of climb is a
    documented, overridable mission assumption (``roc_m_s``, default 7.62 m/s
    = 1500 ft/min, a typical transport climb rate at constant Mach); it
    describes the manoeuvre being requested, not the aircraft.

    Until 2026-09-10 the thrust term was ``W*g0*(dh/V)``, whose units are N*s,
    added to drag in N, with an undocumented 5% thrust floor and a per-step
    fuel clamp that hid the consequences. Climb fuel was several times too
    high as a result.
    """
    N_STEPS = 50
    dh = (end_altitude_m - start_altitude_m) / N_STEPS
    roc = max(abs(float(roc_m_s)), 0.1)
    W = weight_kg
    total_fuel = 0.0
    total_time = 0.0
    total_dist = 0.0

    for i in range(N_STEPS):
        h = start_altitude_m + i * dh
        V = max(mach_to_tas(mach, h), 1.0)
        D = _drag(W, cd0, k, wing_area_m2, mach, h)

        sin_gamma = math.copysign(roc, dh) / V
        sin_gamma = max(-0.5, min(0.5, sin_gamma))
        cos_gamma = math.sqrt(1.0 - sin_gamma * sin_gamma)

        T_required = max(D + W * G0 * sin_gamma, 0.0)  # N
        dt = abs(dh) / roc  # s

        fuel_step = T_required * tsfc_1_per_s * dt  # N * kg/(N s) * s = kg
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

    ``tsfc_1_per_s`` is mass-based, kg/(N*s): fuel mass flow per newton of
    thrust, which is what the propulsion stage supplies. With a mass-based
    TSFC the weight in the Breguet exponent is m*g0, so g0 appears explicitly:

        dm/dt = -tsfc * T,  T = D = m*g0 / (L/D)
        m_end = m_start * exp(-R * tsfc * g0 / (V * L/D))

    Until 2026-09-10 this omitted g0, which was consistent only with a
    weight-based TSFC in 1/s. The propulsion adapter switched to mass-based
    TSFC in August (the lbm/lbf fix) and this exponent was not updated, so
    cruise fuel was understated by a factor of about g0.
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

    exponent = -distance_m * tsfc_1_per_s * G0 / (V * L_over_D)
    # An exponent below -2 means burning more than 86% of the aircraft to get
    # there: the mission is infeasible, not a numerical problem. Clamped so the
    # caller still gets a finite, obviously-wrong number rather than an overflow.
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


def min_drag_speed(
    weight_kg: float,
    altitude_m: float,
    cd0: float,
    k: float,
    wing_area_m2: float,
) -> float:
    """True airspeed [m/s] for level flight at the minimum-drag point of the polar.

    CD = cd0 + k*CL^2 has its best lift-to-drag ratio where the induced drag
    equals the zero-lift drag, CL_md = sqrt(cd0 / k). Lift = weight at that
    CL fixes the speed: V = sqrt(2*m*g0 / (rho*S*CL_md)).

    Raises ``ValueError`` when cd0 or k is not positive, because the polar
    then has no minimum-drag point.
    """
    if cd0 <= 0.0 or k <= 0.0:
        raise ValueError(
            f"The polar has no minimum-drag point unless cd0 and k are positive; got cd0={cd0!r}, k={k!r}."
        )
    if weight_kg <= 0.0 or wing_area_m2 <= 0.0:
        raise ValueError(f"Need a positive weight and wing area; got weight_kg={weight_kg!r}, S={wing_area_m2!r}.")
    cl_md = math.sqrt(cd0 / k)
    rho = isa(altitude_m).density_kg_m3
    return math.sqrt(2.0 * weight_kg * G0 / (rho * wing_area_m2 * cl_md))


def hold_segment(
    weight_kg: float,
    altitude_m: float,
    duration_s: float,
    cd0: float,
    k: float,
    wing_area_m2: float,
    tsfc_1_per_s: float,
    **_: Any,
) -> SegmentResult:
    """Hold at constant altitude for a fixed time at the minimum-drag speed.

    The aircraft flies at CL_md = sqrt(cd0 / k) (see :func:`min_drag_speed`),
    where the lift-to-drag ratio is at its maximum,

        (L/D)_max = 1 / (2*sqrt(cd0*k)),

    so level flight needs T = D = m*g0 / (L/D)_max and the fuel is
    T * tsfc * duration. The drag is evaluated at the entry weight: a hold
    burns a small fraction of the aircraft, so the change of weight over the
    hold is neglected rather than integrated. A hold is flown around a fix
    and earns no range credit, so ``distance_m`` is zero.
    """
    if duration_s < 0.0:
        raise ValueError(f"Hold duration must not be negative; got {duration_s!r} s.")
    if cd0 <= 0.0 or k <= 0.0:
        raise ValueError(
            f"The polar has no minimum-drag point unless cd0 and k are positive; got cd0={cd0!r}, k={k!r}."
        )
    ld_max = 1.0 / (2.0 * math.sqrt(cd0 * k))
    drag_n = weight_kg * G0 / ld_max
    fuel = drag_n * tsfc_1_per_s * duration_s
    return SegmentResult(
        segment_type="hold",
        fuel_burned_kg=fuel,
        distance_m=0.0,
        time_s=duration_s,
        start_weight_kg=weight_kg,
        end_weight_kg=weight_kg - fuel,
        start_altitude_m=altitude_m,
        end_altitude_m=altitude_m,
    )


SEGMENT_DISPATCH: dict[str, Any] = {
    "taxi": taxi_segment,
    "takeoff": takeoff_segment,
    "climb": climb_segment,
    "cruise": cruise_segment,
    "descent": descent_segment,
    "approach": approach_segment,
    "landing": landing_segment,
    "hold": hold_segment,
}
