"""Take-off weight from the file's own mass breakdown, labelled, never defaulted."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from nseg_mcp.cpacs_adapter import read_from_cpacs, run_adapter
from tests.cpacs_fixture import MTOM_KG, WEIGHT_KG, make_cpacs

MTOM_SOURCE = "cpacs:analyses/massBreakdown/designMasses/mTOM/mass"


def test_weight_comes_from_the_files_mtom_when_the_caller_gives_none() -> None:
    xml = make_cpacs(mtom_kg=MTOM_KG)
    out_xml, res = run_adapter(xml, {"range_m": 2_000_000.0})

    assert res["success"] is True
    assert res["initial_weight_kg"] == MTOM_KG
    assert res["weight_source"] == MTOM_SOURCE

    node = ET.fromstring(out_xml).find(".//vehicles/aircraft/model/analysisResults/mission")
    assert node.findtext("weightSource") == MTOM_SOURCE
    assert float(node.findtext("initialWeightKg")) == MTOM_KG


def test_read_from_cpacs_reports_the_weight_source() -> None:
    inputs = read_from_cpacs(make_cpacs(mtom_kg=MTOM_KG), None)
    assert inputs["weight_kg"] == MTOM_KG
    assert inputs["weight_source"] == MTOM_SOURCE

    inputs = read_from_cpacs(make_cpacs(mtom_kg=None), None)
    assert inputs["weight_kg"] is None
    assert inputs["weight_source"] is None


def test_no_weight_anywhere_is_still_a_structured_error() -> None:
    xml = make_cpacs(mtom_kg=None)
    out_xml, res = run_adapter(xml, {"range_m": 2_000_000.0})

    assert res["success"] is False
    assert res["error"]["type"] == "missing_input"
    assert "weight_kg" in res["error"]["message"]
    assert "mTOM" in res["error"]["details"]
    assert out_xml == xml


def test_callers_weight_takes_precedence_over_the_files_mtom() -> None:
    _, res = run_adapter(make_cpacs(mtom_kg=MTOM_KG), {"weight_kg": WEIGHT_KG})
    assert res["initial_weight_kg"] == WEIGHT_KG
    assert res["weight_source"] == "mission_profile:weight_kg"


def test_ata_profile_also_flies_at_the_files_mtom() -> None:
    profile = {"rules": "ATA", "diversion_altitude_ft": 25_000.0, "taxi_fuel_flow_kg_s": 0.2}
    _, res = run_adapter(make_cpacs(mtom_kg=MTOM_KG), profile)
    assert res["success"] is True
    assert res["initial_weight_kg"] == MTOM_KG
    assert res["weight_source"] == MTOM_SOURCE
