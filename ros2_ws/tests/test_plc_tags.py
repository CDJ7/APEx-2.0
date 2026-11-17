"""The station's PLC write allow-list: the software side of 'we never write safety tags'."""

from pathlib import Path

import pytest

from apex_cell_sim.cell_model import CellModel
from apex_plc_bridge.client import InProcessBackend, PlcClient
from apex_plc_bridge.tags import TagSpec, TagWriteRefused, is_denied

TAGS = Path(__file__).resolve().parents[1] / "src" / "apex_bringup" / "config" / "plc_tags.yaml"


@pytest.fixture()
def spec() -> TagSpec:
    return TagSpec.load(TAGS)


def test_only_from_station_members_are_writable(spec):
    for t in spec.writable():
        assert t.path.startswith("APEX_FromStation.")
    for name in ("Job_Ack", "Load_Complete", "Safety_OK", "KUKA_Mode", "PLC_Heartbeat"):
        with pytest.raises(TagWriteRefused):
            spec.check_write(name)


@pytest.mark.parametrize("name", ["NHE", "BS", "QBS", "SHS1", "SHS2", "US2", "SBH", "AF", "FF", "SafetyReset",
                                  "Estop_Bypass", "Guard_Door", "S:FS", "$OV_PRO", "OV_PRO", "LightCurtain_Mute",
                                  "KUKA_Mode"])
def test_safety_names_are_denied(name):
    assert is_denied(name)


def test_yaml_cannot_add_a_safety_write(spec):
    raw = dict(spec.raw)
    raw["from_station"] = dict(raw["from_station"]) | {"Safety_Reset": {"type": "BOOL"}}
    with pytest.raises(ValueError):
        TagSpec(raw)
    raw2 = dict(spec.raw) | {"write_prefix": "Program:Safety."}
    with pytest.raises(ValueError):
        TagSpec(raw2)


def test_unknown_tag_refused_and_nothing_sent(spec):
    sent = []

    class Spy:
        def connect(self): ...
        def close(self): ...
        def read(self, paths): return {}
        def write(self, path, value): sent.append(path)

    c = PlcClient(spec, Spy())
    for bad in ("Safety_OK", "APEX_ToStation.Load_Complete", "Program:MainProgram.Anything", "SHS1"):
        with pytest.raises(TagWriteRefused):
            c.write(bad, 1)
    assert sent == []


def test_round_trip_through_cell_model(spec):
    cell = CellModel(spec)
    c = PlcClient(spec, InProcessBackend(cell))
    c.write("Job_ID", 7)
    c.write("Job_PartCodes", [3, 4])
    tags = c.read_all()
    assert tags["Job_ID"] == 7 and tags["Job_PartCodes"][:3] == [3, 4, 0]
    assert tags["Safety_Simulated"] is True        # the simulator always says it is a simulator
    with pytest.raises(ValueError):
        c.write("Job_PartCodes", list(range(11)))  # longer than the PLC array


def test_cell_rejects_writes_outside_station_block(spec):
    cell = CellModel(spec)
    with pytest.raises(PermissionError):
        cell.station_write("APEX_ToStation.Load_Complete", True)
    with pytest.raises(PermissionError):
        cell.station_write("APEX_ToStation.Safety_OK", False)
