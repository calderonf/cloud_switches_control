"""Headless PoE control for cloud-managed switches."""

from .cloud_managed_switch import (
    DEFAULT_SWITCH_PASSWORD,
    DEFAULT_PORT_INDEX,
    CloudManagedSwtch,
    SwitchAuthError,
    SwitchCommandError,
    SwitchError,
    SwitchProtocolError,
    SwitchResolutionError,
    SwitchTransportError,
    normalize_mac,
    opcode_for_poe,
    poe_state_for_port,
    resolve_host_for_mac,
)

__all__ = [
    "CloudManagedSwtch",
    "DEFAULT_SWITCH_PASSWORD",
    "DEFAULT_PORT_INDEX",
    "SwitchAuthError",
    "SwitchCommandError",
    "SwitchError",
    "SwitchProtocolError",
    "SwitchResolutionError",
    "SwitchTransportError",
    "normalize_mac",
    "opcode_for_poe",
    "poe_state_for_port",
    "resolve_host_for_mac",
]
