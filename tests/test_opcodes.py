import logging

import pytest

from cloud_switches_control.cli import (
    _extract_status_output,
    _normalize_legacy_target_argv,
    _resolve_log_level,
)
from cloud_switches_control.cloud_managed_switch import (
    CloudManagedSwtch,
    DEFAULT_PORT_INDEX,
    DEFAULT_SWITCH_PASSWORD,
    SwitchResolutionError,
    normalize_mac,
    opcode_for_poe,
    poe_state_for_port,
    resolve_host_for_mac,
)


@pytest.mark.parametrize(
    ("port", "off_opcode", "on_opcode"),
    [
        (1, 50, 562),
        (2, 34, 546),
        (3, 18, 530),
        (4, 2, 514),
    ],
)
def test_default_port_opcode_table(port: int, off_opcode: int, on_opcode: int) -> None:
    assert opcode_for_poe(port, False) == off_opcode
    assert opcode_for_poe(port, True) == on_opcode


def test_default_port_index_matches_known_switch() -> None:
    assert DEFAULT_PORT_INDEX == (3, 2, 1, 0)


def test_default_switch_password_matches_factory_default() -> None:
    assert DEFAULT_SWITCH_PASSWORD == "123456"


def test_custom_port_index_is_supported() -> None:
    assert opcode_for_poe(1, False, [0, 1, 2, 3]) == 2
    assert opcode_for_poe(4, True, [0, 1, 2, 3]) == 562


def test_invalid_port_raises() -> None:
    with pytest.raises(ValueError):
        opcode_for_poe(0, True)

    with pytest.raises(ValueError):
        opcode_for_poe(5, False)


def test_session_cookie_is_loaded_from_cache(tmp_path) -> None:
    cache_file = tmp_path / "switch.cookie"
    cache_file.write_text("cached-cookie=", encoding="utf-8")

    switch = CloudManagedSwtch("192.168.0.220", password="secret", session_cache_path=cache_file)

    assert switch._cookie == "cached-cookie="


def test_session_cookie_is_saved_to_cache(tmp_path) -> None:
    cache_file = tmp_path / "switch.cookie"
    switch = CloudManagedSwtch("192.168.0.220", password="secret", session_cache_path=cache_file)

    switch._cookie = "new-cookie="
    switch._save_cached_cookie()

    assert cache_file.read_text(encoding="utf-8") == "new-cookie="


def test_mac_mode_loads_cookie_from_host_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "cloud_switches_control.cloud_managed_switch.resolve_host_for_mac",
        lambda mac, **kwargs: "192.168.0.220",
    )

    host_switch = CloudManagedSwtch("192.168.0.220", password="secret")
    host_switch._cookie = "shared-cookie="
    host_switch._save_cached_cookie()

    mac_switch = CloudManagedSwtch.from_mac("5C15C5088EDA", password="secret")

    assert mac_switch._cookie == "shared-cookie="


def test_mac_mode_saves_cookie_for_both_mac_and_host(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "cloud_switches_control.cloud_managed_switch.resolve_host_for_mac",
        lambda mac, **kwargs: "192.168.0.220",
    )

    mac_switch = CloudManagedSwtch.from_mac("5C15C5088EDA", password="secret")
    mac_switch._cookie = "shared-cookie="
    mac_switch._save_cached_cookie()

    host_switch = CloudManagedSwtch("192.168.0.220", password="secret")

    assert host_switch._cookie == "shared-cookie="


def test_poe_state_uses_internal_port_order() -> None:
    status = {"poec": [1, 1, 1, 0]}

    assert poe_state_for_port(status, 1, [3, 2, 1, 0]) == 0
    assert poe_state_for_port(status, 4, [3, 2, 1, 0]) == 1


def test_normalize_mac_accepts_common_formats() -> None:
    assert normalize_mac("5C15C5088EDA") == "5C15C5088EDA"
    assert normalize_mac("5c:15:c5:08:8e:da") == "5C15C5088EDA"
    assert normalize_mac("5c:15:c5:8:8e:da") == "5C15C5088EDA"
    assert normalize_mac("5c-15-c5-08-8e-da") == "5C15C5088EDA"
    assert normalize_mac("5c15.c508.8eda") == "5C15C5088EDA"


def test_resolve_host_for_mac_uses_ip_neigh_output() -> None:
    host = resolve_host_for_mac(
        "5C15C5088EDA",
        ip_neigh_text="192.168.0.220 dev eth0 lladdr 5c:15:c5:08:8e:da REACHABLE\n",
        proc_net_arp_text="",
        arp_an_text="",
    )

    assert host == "192.168.0.220"


def test_resolve_host_for_mac_uses_proc_net_arp_output() -> None:
    host = resolve_host_for_mac(
        "5C15C5088EDA",
        ip_neigh_text="",
        proc_net_arp_text=(
            "IP address       HW type     Flags       HW address            Mask     Device\n"
            "192.168.0.220    0x1         0x2         5c:15:c5:08:8e:da     *        eth0\n"
        ),
        arp_an_text="",
    )

    assert host == "192.168.0.220"


def test_resolve_host_for_mac_raises_when_not_found() -> None:
    with pytest.raises(SwitchResolutionError):
        resolve_host_for_mac("5C15C5088EDA", ip_neigh_text="", proc_net_arp_text="", arp_an_text="")


def test_switch_can_be_created_from_mac(monkeypatch) -> None:
    monkeypatch.setattr(
        "cloud_switches_control.cloud_managed_switch.resolve_host_for_mac",
        lambda mac, **kwargs: "192.168.0.220",
    )

    switch = CloudManagedSwtch.from_mac("5C15C5088EDA", password="secret")

    assert switch.host == "192.168.0.220"
    assert switch.mac == "5C15C5088EDA"


def test_switch_uses_default_password_when_omitted() -> None:
    switch = CloudManagedSwtch("192.168.0.220")

    assert switch.password == "123456"


def test_login_logs_initialization_when_switch_is_not_active(monkeypatch, caplog) -> None:
    switch = CloudManagedSwtch("192.168.0.220")
    monkeypatch.setattr(switch, "detect_activation_state", lambda: "not_active")
    monkeypatch.setattr(
        switch,
        "_request_json",
        lambda *args, **kwargs: {"login": "success"},
    )

    with caplog.at_level(logging.INFO):
        switch.login()

    assert "switch not active, initializing password using default switch password" in caplog.text
    assert "login: success using default switch password" in caplog.text


def test_detect_activation_state_returns_none_when_probe_fails(monkeypatch) -> None:
    switch = CloudManagedSwtch("192.168.0.220")

    def raise_error(*args, **kwargs):
        raise SwitchResolutionError("probe failed")

    monkeypatch.setattr(switch, "_request_json", raise_error)

    assert switch.detect_activation_state() is None


def test_detect_activation_state_reads_callcmd_100(monkeypatch) -> None:
    switch = CloudManagedSwtch("192.168.0.220")

    monkeypatch.setattr(
        switch,
        "_request_json",
        lambda *args, **kwargs: {"Active_state": "active"},
    )

    assert switch.detect_activation_state() == "active"


def test_cli_rewrites_positional_host_target() -> None:
    assert _normalize_legacy_target_argv(["192.168.0.220", "status"]) == [
        "--host",
        "192.168.0.220",
        "status",
    ]


def test_cli_rewrites_positional_mac_target() -> None:
    assert _normalize_legacy_target_argv(["5C15C5088EDA", "restart", "--port", "2"]) == [
        "--mac",
        "5C15C5088EDA",
        "restart",
        "--port",
        "2",
    ]


def test_extract_status_output_returns_full_status_by_default() -> None:
    class Args:
        tx = rx = poec = link = pw = tp = vol = False

    status = {"tx": ["1"], "vol": "52.8"}

    assert _extract_status_output(Args(), status) == status


def test_extract_status_output_returns_requested_field() -> None:
    class Args:
        tx = True
        rx = poec = link = pw = tp = vol = False

    status = {"tx": ["0", "0", "56", "0", "40"], "vol": "52.8"}

    assert _extract_status_output(Args(), status) == ["0", "0", "56", "0", "40"]


def test_resolve_log_level_defaults_to_warning() -> None:
    class Args:
        log_level = None
        verbose = False

    assert _resolve_log_level(Args()) == "WARNING"


def test_resolve_log_level_uses_info_for_verbose() -> None:
    class Args:
        log_level = None
        verbose = True

    assert _resolve_log_level(Args()) == "INFO"
