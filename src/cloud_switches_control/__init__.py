"""Headless LAN PoE control for managed switches."""

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
    port_telemetry_for_port,
    poe_state_for_port,
    resolve_host_for_mac,
)

# Preferred generic public name. CloudManagedSwtch remains available for
# backwards compatibility with earlier releases.
ManagedSwitch = CloudManagedSwtch

__all__ = [
    "CloudManagedSwtch",
    "ManagedSwitch",
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
    "port_telemetry_for_port",
    "poe_state_for_port",
    "resolve_host_for_mac",
]
