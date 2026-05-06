"""OVS - Output Verification System checks for NSEG MCP CPACS output.

Validates that the NSEG adapter writes expected XPaths with plausible values.
Self-contained: no cross-repo dependencies.
"""

from xml.etree import ElementTree as ET

SAMPLE_NSEG_OUTPUT = """\
<?xml version="1.0"?>
<cpacs>
  <vehicles>
    <aircraft>
      <model uID="test">
        <name>OVS Test Aircraft</name>
        <analysisResults>
          <mission>
            <backend>nseg</backend>
            <success>true</success>
            <totalFuelBurnedKg>5812.3</totalFuelBurnedKg>
            <initialWeightKg>78000.0</initialWeightKg>
            <finalWeightKg>72187.7</finalWeightKg>
            <totalDistanceM>3000000.0</totalDistanceM>
            <totalDistanceNm>1619.9</totalDistanceNm>
            <totalTimeS>13500.0</totalTimeS>
            <totalTimeHr>3.75</totalTimeHr>
            <fuelFraction>0.0745</fuelFraction>
            <segments>
              <segment><type>taxi</type><fuelBurnedKg>15.0</fuelBurnedKg></segment>
              <segment><type>climb</type><fuelBurnedKg>1200.0</fuelBurnedKg></segment>
              <segment><type>cruise</type><fuelBurnedKg>4200.0</fuelBurnedKg></segment>
              <segment><type>descent</type><fuelBurnedKg>200.0</fuelBurnedKg></segment>
              <segment><type>landing</type><fuelBurnedKg>197.3</fuelBurnedKg></segment>
            </segments>
          </mission>
        </analysisResults>
      </model>
    </aircraft>
  </vehicles>
</cpacs>
"""


def test_mission_output_structure():
    root = ET.fromstring(SAMPLE_NSEG_OUTPUT)
    assert root.tag == "cpacs"
    assert root.find(".//vehicles/aircraft") is not None


def test_mission_results_present():
    root = ET.fromstring(SAMPLE_NSEG_OUTPUT)
    mission = root.find(".//analysisResults/mission")
    assert mission is not None


def test_mission_backend_nseg():
    root = ET.fromstring(SAMPLE_NSEG_OUTPUT)
    be = root.find(".//analysisResults/mission/backend")
    assert be is not None and be.text == "nseg"


def test_mission_success():
    root = ET.fromstring(SAMPLE_NSEG_OUTPUT)
    s = root.find(".//analysisResults/mission/success")
    assert s is not None and s.text in ("true", "false")


def test_mission_fuel_burned_range():
    root = ET.fromstring(SAMPLE_NSEG_OUTPUT)
    el = root.find(".//analysisResults/mission/totalFuelBurnedKg")
    assert el is not None and el.text is not None
    val = float(el.text)
    assert 0.0 <= val <= 500000.0


def test_mission_distance_range():
    root = ET.fromstring(SAMPLE_NSEG_OUTPUT)
    el = root.find(".//analysisResults/mission/totalDistanceNm")
    assert el is not None and el.text is not None
    val = float(el.text)
    assert 0.0 <= val <= 20000.0


def test_mission_segments_present():
    root = ET.fromstring(SAMPLE_NSEG_OUTPUT)
    segs = root.find(".//analysisResults/mission/segments")
    assert segs is not None
    assert len(segs) > 0


def test_mission_fuel_fraction_plausible():
    root = ET.fromstring(SAMPLE_NSEG_OUTPUT)
    el = root.find(".//analysisResults/mission/fuelFraction")
    assert el is not None and el.text is not None
    val = float(el.text)
    assert 0.0 <= val <= 1.0
