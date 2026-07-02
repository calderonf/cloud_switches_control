from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Optional, Sequence

from .cloud_managed_switch import (
    DEFAULT_SWITCH_PASSWORD,
    CloudManagedSwtch,
    SwitchError,
    opcode_for_poe,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="switch-poe",
        description="Headless PoE control for cloud-managed switches",
    )
    parser.add_argument("--host", help="Switch IP or hostname")
    parser.add_argument("--mac", help="Switch MAC address, e.g. 5C15C5088EDA")
    parser.add_argument(
        "--password",
        default=os.environ.get("SWITCH_PASSWORD", DEFAULT_SWITCH_PASSWORD),
        help=(
            "Switch password. Defaults to SWITCH_PASSWORD if set, otherwise uses "
            f"the switch factory default {DEFAULT_SWITCH_PASSWORD}."
        ),
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=80,
        help="HTTP port used by the switch (default: 80)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-request timeout in seconds (default: 5)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="How many times to retry each command (default: 3)",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=1.0,
        help="Sleep between retries in seconds (default: 1)",
    )
    parser.add_argument(
        "--port-index",
        default="3,2,1,0",
        help="Comma-separated physical-to-internal port map (default: 3,2,1,0)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Explicit log verbosity override (default: WARNING, or INFO with --verbose)",
    )
    parser.add_argument(
        "-v",
        "--v",
        "--verbose",
        action="store_true",
        help="Show informative logs such as MAC resolution and login success",
    )
    parser.add_argument(
        "--command-settle-delay",
        type=float,
        default=1.0,
        help="Seconds to wait after a PoE change before polling status (default: 1)",
    )
    parser.add_argument(
        "--verify-timeout",
        type=float,
        default=15.0,
        help="How long to poll for the expected PoE state (default: 15)",
    )
    parser.add_argument(
        "--verify-interval",
        type=float,
        default=1.0,
        help="Seconds between verification polls (default: 1)",
    )
    parser.add_argument(
        "--session-cache-path",
        default=os.environ.get("SWITCH_SESSION_CACHE"),
        help=(
            "Path to persist the switch session cookie across invocations. "
            "Defaults to XDG cache or ~/.cache. Use --no-session-cache to disable."
        ),
    )
    parser.add_argument(
        "--no-session-cache",
        action="store_true",
        help="Disable persisted session-cookie reuse across invocations",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Read switch status")
    status_fields = status_parser.add_mutually_exclusive_group(required=False)
    status_fields.add_argument("--tx", action="store_true", help="Return only the tx counters list")
    status_fields.add_argument("--rx", action="store_true", help="Return only the rx counters list")
    status_fields.add_argument("--poec", action="store_true", help="Return only the PoE state list")
    status_fields.add_argument("--link", action="store_true", help="Return only the link-state list")
    status_fields.add_argument("--pw", action="store_true", help="Return only the per-port power list")
    status_fields.add_argument("--tp", action="store_true", help="Return only total power usage")
    status_fields.add_argument("--vol", action="store_true", help="Return only the reported voltage")

    poe_parser = subparsers.add_parser("poe", help="Enable or disable PoE on one port")
    poe_parser.add_argument("--port", type=int, required=True, help="Physical PoE port number")
    poe_mode = poe_parser.add_mutually_exclusive_group(required=True)
    poe_mode.add_argument("--on", action="store_true", help="Turn PoE on")
    poe_mode.add_argument("--off", action="store_true", help="Turn PoE off")

    restart_parser = subparsers.add_parser("restart", help="Power-cycle PoE on one port")
    restart_parser.add_argument("--port", type=int, required=True, help="Physical PoE port number")
    restart_parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="How long to keep PoE off before turning it back on (default: 5)",
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = _normalize_legacy_target_argv(argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.host and not args.mac:
        parser.error("one of --host or --mac is required")

    logging.basicConfig(
        level=getattr(logging, _resolve_log_level(args)),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        try:
            port_index = _parse_port_index(args.port_index)
        except ValueError as exc:
            parser.error(str(exc))

        switch = CloudManagedSwtch(
            args.host,
            password=args.password,
            mac=args.mac,
            http_port=args.http_port,
            timeout=args.timeout,
            retries=args.retries,
            retry_delay=args.retry_delay,
            port_index=port_index,
            command_settle_delay=args.command_settle_delay,
            verify_timeout=args.verify_timeout,
            verify_interval=args.verify_interval,
            session_cache_path=None if args.no_session_cache else args.session_cache_path,
        )

        if args.command == "status":
            result = _extract_status_output(args, switch.status())
        elif args.command == "poe":
            enabled = bool(args.on)
            result = {
                "port": args.port,
                "enabled": enabled,
                "opcode": opcode_for_poe(args.port, enabled, switch.port_index),
                "status": switch.set_poe(args.port, enabled, verify=True),
            }
        elif args.command == "restart":
            result = {
                "port": args.port,
                "delay": args.delay,
                "off_opcode": opcode_for_poe(args.port, False, switch.port_index),
                "on_opcode": opcode_for_poe(args.port, True, switch.port_index),
                "status": switch.restart_poe(args.port, delay=args.delay),
            }
        else:  # pragma: no cover - argparse enforces valid commands
            parser.error(f"unsupported command: {args.command}")

        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (SwitchError, ValueError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1


def _parse_port_index(value: str) -> tuple[int, ...]:
    try:
        parsed = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("--port-index must be a comma-separated list of integers") from exc
    if not parsed:
        raise ValueError("--port-index cannot be empty")
    return parsed


def _resolve_log_level(args: argparse.Namespace) -> str:
    if args.log_level:
        return args.log_level
    if getattr(args, "verbose", False):
        return "INFO"
    return "WARNING"


def _extract_status_output(args: argparse.Namespace, status: dict) -> object:
    for field_name in ("tx", "rx", "poec", "link", "pw", "tp", "vol"):
        if getattr(args, field_name, False):
            return status.get(field_name)
    return status


def _normalize_legacy_target_argv(argv: Optional[Sequence[str]]) -> Optional[list[str]]:
    if argv is None:
        raw_args = sys.argv[1:]
    else:
        raw_args = list(argv)

    if len(raw_args) >= 2 and not raw_args[0].startswith("-") and raw_args[1] in {"status", "poe", "restart"}:
        target = raw_args[0]
        option = "--mac" if _looks_like_mac(target) else "--host"
        return [option, target, *raw_args[1:]]

    return raw_args


def _looks_like_mac(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    if ":" in candidate or "-" in candidate:
        return True
    if "." in candidate:
        dot_chunks = candidate.split(".")
        if len(dot_chunks) == 4 and all(chunk.isdigit() for chunk in dot_chunks):
            return False
        return True
    compact = "".join(char for char in candidate if char.isalnum())
    return len(compact) == 12 and all(char in "0123456789abcdefABCDEF" for char in compact)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
