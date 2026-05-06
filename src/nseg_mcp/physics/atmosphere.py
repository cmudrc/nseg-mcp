"""ISA 1976 Standard Atmosphere model.

Implements the International Standard Atmosphere used by NSEG and most
aircraft performance codes.  Covers altitudes from sea level to 86 km
with the troposphere/stratosphere/mesosphere layers.

Reference:
    U.S. Standard Atmosphere, 1976 (NOAA/NASA/USAF).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Sea-level reference values
T0 = 288.15  # K    – sea-level temperature
P0 = 101325.0  # Pa   – sea-level pressure
RHO0 = 1.225  # kg/m^3  – sea-level density
A0 = 340.294  # m/s  – sea-level speed of sound
G0 = 9.80665  # m/s^2
R_AIR = 287.0528  # J/(kg·K) – specific gas constant for dry air
GAMMA = 1.4  # ratio of specific heats for air

# Layer definitions: (base altitude [m], lapse rate [K/m])
_LAYERS: list[tuple[float, float]] = [
    (0.0, -0.0065),  # Troposphere
    (11000.0, 0.0),  # Tropopause / Lower stratosphere
    (20000.0, 0.001),  # Upper stratosphere
    (32000.0, 0.0028),  # Upper stratosphere (continued)
    (47000.0, 0.0),  # Stratopause
    (51000.0, -0.0028),  # Mesosphere
    (71000.0, -0.002),  # Upper mesosphere
]

# Pre-compute base temperature and pressure at each layer boundary.
_LAYER_T: list[float] = [T0]
_LAYER_P: list[float] = [P0]

for i in range(1, len(_LAYERS)):
    h_prev, lapse = _LAYERS[i - 1]
    h_cur = _LAYERS[i][0]
    dh = h_cur - h_prev
    T_prev = _LAYER_T[-1]
    P_prev = _LAYER_P[-1]

    T_new = T_prev + lapse * dh
    if abs(lapse) < 1e-12:
        P_new = P_prev * math.exp(-G0 * dh / (R_AIR * T_prev))
    else:
        P_new = P_prev * (T_new / T_prev) ** (-G0 / (lapse * R_AIR))

    _LAYER_T.append(T_new)
    _LAYER_P.append(P_new)


@dataclass(frozen=True, slots=True)
class AtmosphereState:
    """Atmospheric conditions at a given altitude."""

    altitude_m: float
    temperature_k: float
    pressure_pa: float
    density_kg_m3: float
    speed_of_sound_m_s: float

    @property
    def temperature_c(self) -> float:
        return self.temperature_k - 273.15


def isa(altitude_m: float) -> AtmosphereState:
    """Compute ISA conditions at a geopotential altitude.

    Parameters
    ----------
    altitude_m : float
        Geopotential altitude in metres (0 .. 86 000 m).

    Returns
    -------
    AtmosphereState
    """
    h = max(0.0, altitude_m)
    layer_idx = 0
    for i in range(len(_LAYERS) - 1, -1, -1):
        if h >= _LAYERS[i][0]:
            layer_idx = i
            break

    h_base, lapse = _LAYERS[layer_idx]
    T_base = _LAYER_T[layer_idx]
    P_base = _LAYER_P[layer_idx]
    dh = h - h_base

    T = T_base + lapse * dh

    if abs(lapse) < 1e-12:
        P = P_base * math.exp(-G0 * dh / (R_AIR * T_base))
    else:
        P = P_base * (T / T_base) ** (-G0 / (lapse * R_AIR))

    rho = P / (R_AIR * T)
    a = math.sqrt(GAMMA * R_AIR * T)

    return AtmosphereState(
        altitude_m=altitude_m,
        temperature_k=T,
        pressure_pa=P,
        density_kg_m3=rho,
        speed_of_sound_m_s=a,
    )


def mach_to_tas(mach: float, altitude_m: float) -> float:
    """Convert Mach number to true airspeed [m/s] at a given altitude."""
    return mach * isa(altitude_m).speed_of_sound_m_s


def dynamic_pressure(mach: float, altitude_m: float) -> float:
    """Compute dynamic pressure q = 0.5 * rho * V^2  [Pa]."""
    atm = isa(altitude_m)
    v = mach * atm.speed_of_sound_m_s
    return 0.5 * atm.density_kg_m3 * v * v
