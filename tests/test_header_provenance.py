"""Every CPACS write appends one <update> under header/updates."""

from __future__ import annotations

import importlib.metadata
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import pytest

from nseg_mcp import cpacs_adapter
from nseg_mcp.cpacs_adapter import run_adapter
from tests.cpacs_fixture import EXISTING_UPDATES, WEIGHT_KG, make_cpacs

PROFILE = {"weight_kg": WEIGHT_KG, "range_m": 2_000_000.0}
ATA_PROFILE = {**PROFILE, "rules": "ATA", "diversion_altitude_ft": 25_000.0}
CHILD_ORDER = ["modification", "creator", "timestamp", "version", "cpacsVersion"]


def _header(xml: str) -> ET.Element:
    header = ET.fromstring(xml).find("header")
    assert header is not None
    return header


def test_write_appends_a_provenance_entry_after_the_existing_ones() -> None:
    xml = make_cpacs()
    before = _header(xml)
    started = datetime.now(UTC).replace(microsecond=0)

    out_xml, res = run_adapter(xml, PROFILE)
    assert res["success"] is True

    header = _header(out_xml)
    entries = header.findall("updates/update")
    assert len(entries) == len(EXISTING_UPDATES) + 1
    assert [e.findtext("modification") for e in entries[:-1]] == list(EXISTING_UPDATES)

    new = entries[-1]
    assert [c.tag for c in new] == CHILD_ORDER
    modification = new.findtext("modification")
    assert modification.startswith("nseg-mcp wrote")
    assert "analysisResults/mission" in modification
    assert modification.endswith(".") and modification.count(". ") == 0  # one sentence
    assert new.findtext("creator") == f"nseg-mcp {importlib.metadata.version('nseg-mcp')}"
    assert new.findtext("creator") != "nseg-mcp unknown"
    assert new.findtext("version") == str(len(EXISTING_UPDATES) + 1)
    assert new.findtext("cpacsVersion") == "3.2"

    stamp = new.findtext("timestamp")
    assert stamp.endswith("Z")
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None
    assert started <= parsed <= datetime.now(UTC)

    # Nothing already in the header was removed, changed or reordered.
    assert [c.tag for c in header] == [c.tag for c in before]
    assert [(c.tag, c.text) for c in header if c.tag != "updates"] == [
        (c.tag, c.text) for c in before if c.tag != "updates"
    ]


def test_a_second_write_increments_the_running_count() -> None:
    xml1, _ = run_adapter(make_cpacs(), PROFILE)
    xml2, _ = run_adapter(xml1, ATA_PROFILE)

    entries = _header(xml2).findall("updates/update")
    assert len(entries) == len(EXISTING_UPDATES) + 2
    assert [e.findtext("version") for e in entries[-2:]] == [
        str(len(EXISTING_UPDATES) + 1),
        str(len(EXISTING_UPDATES) + 2),
    ]
    assert "climb-cruise-descent" in entries[-2].findtext("modification")
    assert "ATA Standard Mission Rules" in entries[-1].findtext("modification")
    # The mission node is replaced, not duplicated, on the second write.
    assert len(ET.fromstring(xml2).findall(".//analysisResults/mission")) == 1


def test_header_without_updates_gets_one_created_after_cpacs_version() -> None:
    out_xml, _ = run_adapter(make_cpacs(with_updates=False), PROFILE)
    header = _header(out_xml)

    tags = [c.tag for c in header]
    assert tags == ["name", "description", "creator", "timestamp", "version", "cpacsVersion", "updates"]
    entries = header.findall("updates/update")
    assert len(entries) == 1
    assert entries[0].findtext("version") == "1"
    assert entries[0].findtext("cpacsVersion") == "3.2"


def test_file_without_a_header_gets_one_first() -> None:
    out_xml, res = run_adapter(make_cpacs(with_header=False), PROFILE)
    assert res["success"] is True
    root = ET.fromstring(out_xml)
    assert root[0].tag == "header"
    entries = root.findall("header/updates/update")
    assert len(entries) == 1
    assert entries[0].findtext("version") == "1"
    assert entries[0].findtext("cpacsVersion") == ""


def test_failed_runs_do_not_touch_the_header() -> None:
    xml = make_cpacs()
    out_xml, res = run_adapter(xml, {})  # no weight anywhere
    assert res["success"] is False
    assert out_xml == xml


def test_creator_falls_back_to_unknown_when_the_package_metadata_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(cpacs_adapter.importlib.metadata, "version", missing)
    out_xml, _ = run_adapter(make_cpacs(), PROFILE)
    assert _header(out_xml).findall("updates/update")[-1].findtext("creator") == "nseg-mcp unknown"
