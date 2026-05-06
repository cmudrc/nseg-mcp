"""Sanity checks for the ISA atmosphere model."""

from nseg_mcp.physics.atmosphere import dynamic_pressure, isa, mach_to_tas


def test_sea_level():
    atm = isa(0.0)
    assert abs(atm.temperature_k - 288.15) < 0.01
    assert abs(atm.pressure_pa - 101325.0) < 1.0
    assert abs(atm.density_kg_m3 - 1.225) < 0.01


def test_tropopause():
    atm = isa(11000.0)
    assert abs(atm.temperature_k - 216.65) < 0.5
    assert 22000 < atm.pressure_pa < 23000


def test_cruise_altitude():
    atm = isa(10668.0)  # 35000 ft
    assert 200 < atm.temperature_k < 230
    assert 20000 < atm.pressure_pa < 30000


def test_mach_to_tas():
    tas = mach_to_tas(0.78, 10668.0)
    assert 220 < tas < 260


def test_dynamic_pressure():
    q = dynamic_pressure(0.78, 10668.0)
    assert q > 0
