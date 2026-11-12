"""Unit tests for the shared protocols in ``common/``."""

import pytest

from common.protocol import messages as m
from common.protocol import modbus_map as mb
from common.protocol import serial_protocol as sp
from common.protocol.states import AgvState, FaultCode, OrderStatus, faults_to_word


# ------------------------------------------------------------ serial protocol
def test_checksum_matches_reference_examples():
    # These exact strings are also documented in docs/PROTOCOL.md and in the sketch.
    assert sp.make_cmd(120, -80) == b"CMD,120,-80*5C\n"
    assert sp.make_stop() == b"STOP*18\n"
    assert sp.make_heartbeat(7) == b"HB,7*11\n"


def test_roundtrip_and_clamp():
    frame = sp.parse_frame(sp.make_cmd(999, -999))
    assert frame.name == "CMD" and frame.ints() == [255, -255]


def test_bad_checksum_rejected():
    with pytest.raises(sp.ProtocolError):
        sp.parse_frame("CMD,1,2*00")
    with pytest.raises(sp.ProtocolError):
        sp.parse_frame("CMD,1,2")


def test_bare_stop_allowed():
    assert sp.parse_frame("STOP\r\n").name == "STOP"
    with pytest.raises(sp.ProtocolError):
        sp.parse_frame("STOP", allow_bare_stop=False)


def test_status_roundtrip():
    st = sp.StatusReport(1234, 100, -50, 10, -5, 512, 300, 1, True, sp.FaultFlags.ESTOP | sp.FaultFlags.WATCHDOG,
                         80, -80, 25100, 42, dock=True, rc=2, act=-1)
    back = sp.StatusReport.from_frame(sp.parse_frame(st.to_frame()))
    assert back == st and back.manual
    # worst-case field widths must still fit in one frame
    big = sp.StatusReport(4294967295, -2147483648, -2147483648, -99999, -99999, 1023, 1023, 3, True,
                          sp.FaultFlags(7), -255, -255, 65535, 65535, True, 2, -1)
    assert len(big.to_frame()) <= sp.MAX_LINE


def test_actuator_frame():
    assert sp.parse_frame(sp.make_actuator(5, 99999)).ints() == [1, 15000]
    assert sp.parse_frame(sp.make_actuator(-1, 3000)).ints() == [-1, 3000]


def test_line_assembler_handles_split_and_overflow():
    asm = sp.LineAssembler(max_line=10)
    assert asm.feed(b"HB,1*") == []
    assert asm.feed(b"25\r\nAB") == ["HB,1*25"]
    assert asm.feed(b"X" * 20 + b"\nOK\n") == ["OK"]
    assert asm.overflows == 1


# ------------------------------------------------------------ JSON messages
def test_message_validation():
    msg = m.make_message(m.MsgType.DISPATCH, {"order_id": 5, "destination": "WS1"}, src="t")
    assert m.decode_message(m.encode_message(msg))["data"]["order_id"] == 5
    bad = m.make_message(m.MsgType.DISPATCH, {"order_id": "5", "destination": "WS1"})
    with pytest.raises(m.MessageError):
        m.validate_message(bad)
    bad = m.make_message(m.MsgType.DISPATCH, {"order_id": True, "destination": "WS1"})
    with pytest.raises(m.MessageError):
        m.validate_message(bad)
    with pytest.raises(m.MessageError):
        m.decode_message(b'{"v":1,"type":"nope"}')


# ------------------------------------------------------------ enums/modbus
def test_enum_codes_stable():
    assert AgvState.from_code(2) is AgvState.NAVIGATING
    assert OrderStatus.DELIVERED.code == 3
    assert faults_to_word({FaultCode.ARDUINO_LINK, FaultCode.LIDAR}) == 0b101


def test_int16_helpers():
    for v in (-32768, -1, 0, 1, 32767):
        assert mb.from_int16(mb.to_int16(v)) == v
    assert mb.to_int16(100000) == 32767
