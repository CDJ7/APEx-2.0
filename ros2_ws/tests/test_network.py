"""Network profiles: the real PLC is never used with the school profile."""

import pytest

from apex_bringup.paths import config_file
from apex_plc_bridge.network import NetworkPolicyError, choose_backend, load_profile


def test_school_profile_refuses_real_plc():
    prof = load_profile(config_file("network_school.yaml"))
    assert choose_backend(prof).kind == "sim"
    with pytest.raises(NetworkPolicyError):
        choose_backend(prof, "logix")


def test_lab_profile_uses_logix_and_kuka_is_never_direct():
    prof = load_profile(config_file("network_lab.yaml"))
    b = choose_backend(prof)
    assert b.kind == "logix" and b.host
    assert prof["kuka"]["allowed"] is False
    assert choose_backend(prof, "sim").kind == "sim"     # sim still allowed on the lab router


def test_unknown_backend():
    with pytest.raises(NetworkPolicyError):
        choose_backend({"plc": {}}, "modbus")
