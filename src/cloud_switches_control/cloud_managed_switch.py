from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

DEFAULT_PORT_INDEX: tuple[int, ...] = (3, 2, 1, 0)
DEFAULT_SWITCH_PASSWORD = "123456"

ACTIVATION_STATE_COMMAND = 100
LOGIN_COMMAND = 123
STATUS_COMMAND = 101
POE_COMMAND = 103
ACTIVATION_STATE_NOT_ACTIVE = "not_active"
ACTIVATION_STATE_ACTIVE = "active"


class SwitchError(RuntimeError):
    """Base error for switch operations."""


class SwitchTransportError(SwitchError):
    """Transport-level communication error."""


class SwitchProtocolError(SwitchError):
    """Malformed or unexpected protocol response."""


class SwitchAuthError(SwitchError):
    """Authentication error."""


class SwitchCommandError(SwitchError):
    """Command execution error."""


class SwitchResolutionError(SwitchError):
    """Failed to resolve a switch IP from a MAC address."""


@dataclass(frozen=True)
class HeaderProfile:
    content_type: str
    include_origin: bool = True
    include_x_requested_with: bool = True
    send_cookie: bool = True


@dataclass(frozen=True)
class HttpResponse:
    status: int
    reason: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


HEADER_PROFILES: tuple[HeaderProfile, ...] = (
    HeaderProfile("application/json; charset=utf-8"),
    HeaderProfile("application/json; charset=UTF-8"),
    HeaderProfile("application/json; charset=utf-8", include_origin=False),
)


def normalize_port_index(port_index: Optional[Sequence[int]]) -> tuple[int, ...]:
    values = DEFAULT_PORT_INDEX if port_index is None else tuple(int(item) for item in port_index)
    if not values:
        raise ValueError("port_index cannot be empty")
    if any(value < 0 for value in values):
        raise ValueError("port_index values must be >= 0")
    return tuple(values)


def normalize_mac(mac: str) -> str:
    value = mac.strip()
    if not value:
        raise ValueError(f"invalid MAC address: {mac!r}")

    separators = (":", "-", ".")
    if any(separator in value for separator in separators):
        if "." in value and ":" not in value and "-" not in value:
            chunks = [chunk for chunk in value.split(".") if chunk]
            if len(chunks) != 3 or any(len(chunk) > 4 or not all(char in "0123456789abcdefABCDEF" for char in chunk) for chunk in chunks):
                raise ValueError(f"invalid MAC address: {mac!r}")
            expanded = [chunk.zfill(4) for chunk in chunks]
            return "".join(part[:2] + part[2:] for part in expanded).upper()

        chunks = [chunk for chunk in value.replace("-", ":").split(":") if chunk]
        if len(chunks) != 6 or any(len(chunk) > 2 or not all(char in "0123456789abcdefABCDEF" for char in chunk) for chunk in chunks):
            raise ValueError(f"invalid MAC address: {mac!r}")
        return "".join(chunk.zfill(2) for chunk in chunks).upper()

    stripped = "".join(char for char in value if char.isalnum()).upper()
    if len(stripped) != 12 or not all(char in "0123456789ABCDEF" for char in stripped):
        raise ValueError(f"invalid MAC address: {mac!r}")
    return stripped


def _mac_matches(left: str, right: str) -> bool:
    return normalize_mac(left) == normalize_mac(right)


def _parse_ip_neigh_text(text: str) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts or "." not in parts[0]:
            continue
        if "lladdr" not in parts:
            continue
        lladdr_index = parts.index("lladdr")
        if lladdr_index + 1 >= len(parts):
            continue
        matches.append((parts[0], parts[lladdr_index + 1]))
    return matches


def _parse_proc_net_arp_text(text: str) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    for index, raw_line in enumerate(text.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        if index == 0 and "IP address" in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        ip_address, hw_address = parts[0], parts[3]
        if "." not in ip_address or hw_address == "00:00:00:00:00:00":
            continue
        matches.append((ip_address, hw_address))
    return matches


def _parse_arp_an_text(text: str) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or " at " not in line or "(" not in line or ")" not in line:
            continue
        ip_address = line.split("(", 1)[1].split(")", 1)[0].strip()
        hw_address = line.split(" at ", 1)[1].split(" ", 1)[0].strip()
        if "." not in ip_address or hw_address in {"<incomplete>", "(incomplete)"}:
            continue
        matches.append((ip_address, hw_address))
    return matches


def _run_command(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout or ""


def _read_proc_net_arp() -> str:
    path = Path("/proc/net/arp")
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def resolve_host_for_mac(
    mac: str,
    *,
    logger: Optional[logging.Logger] = None,
    ip_neigh_text: Optional[str] = None,
    proc_net_arp_text: Optional[str] = None,
    arp_an_text: Optional[str] = None,
) -> str:
    normalized_mac = normalize_mac(mac)
    active_logger = logger or logging.getLogger(__name__)

    candidate_sources = [
        ("ip neigh", _parse_ip_neigh_text, ip_neigh_text, lambda: _run_command(("ip", "-4", "neigh", "show"))),
        ("/proc/net/arp", _parse_proc_net_arp_text, proc_net_arp_text, _read_proc_net_arp),
        ("arp -an", _parse_arp_an_text, arp_an_text, lambda: _run_command(("arp", "-an"))),
    ]

    for source_name, parser, provided_text, loader in candidate_sources:
        text = provided_text if provided_text is not None else loader()
        if not text.strip():
            continue
        for ip_address, candidate_mac in parser(text):
            try:
                if _mac_matches(candidate_mac, normalized_mac):
                    active_logger.info(
                        "resolved mac %s to host %s via %s",
                        normalized_mac,
                        ip_address,
                        source_name,
                    )
                    return ip_address
            except ValueError:
                continue

    raise SwitchResolutionError(
        f"could not resolve host for MAC {normalized_mac}; make sure the switch appears in the local ARP/neighbor table"
    )


def opcode_for_poe(port: int, enabled: bool, port_index: Optional[Sequence[int]] = None) -> int:
    mapping = normalize_port_index(port_index)
    if port < 1 or port > len(mapping):
        raise ValueError(f"port must be between 1 and {len(mapping)}")
    internal_index = mapping[port - 1]
    value = 1 if enabled else 0
    return (value << 9) | (internal_index << 4) | 2


def poe_state_for_port(
    status: dict[str, Any],
    port: int,
    port_index: Optional[Sequence[int]] = None,
) -> int:
    poec = status.get("poec")
    if not isinstance(poec, list):
        raise SwitchProtocolError("status response does not include a valid 'poec' list")
    mapping = normalize_port_index(port_index)
    if port < 1 or port > len(mapping):
        raise SwitchProtocolError(f"port {port} is outside configured port_index range")
    internal_index = mapping[port - 1]
    if internal_index < 0 or internal_index >= len(poec):
        raise SwitchProtocolError(
            f"status response has no PoE state for port {port} (internal index {internal_index})"
        )
    try:
        return int(poec[internal_index])
    except (TypeError, ValueError) as exc:
        raise SwitchProtocolError(
            f"invalid PoE state for port {port}: {poec[internal_index]!r}"
        ) from exc


class CloudManagedSwtch:
    """Headless controller for cloud-managed PoE switches."""

    def __init__(
        self,
        host: Optional[str] = None,
        password: Optional[str] = None,
        *,
        mac: Optional[str] = None,
        http_port: int = 80,
        timeout: float = 5.0,
        retries: int = 3,
        retry_delay: float = 1.0,
        port_index: Optional[Sequence[int]] = None,
        warmup: bool = True,
        command_settle_delay: float = 1.0,
        verify_timeout: float = 15.0,
        verify_interval: float = 1.0,
        session_cache_path: Optional[os.PathLike[str] | str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if not host and not mac:
            raise ValueError("host or mac is required")
        if retries < 1:
            raise ValueError("retries must be >= 1")
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        if retry_delay < 0:
            raise ValueError("retry_delay must be >= 0")
        if command_settle_delay < 0:
            raise ValueError("command_settle_delay must be >= 0")
        if verify_timeout < 0:
            raise ValueError("verify_timeout must be >= 0")
        if verify_interval <= 0:
            raise ValueError("verify_interval must be > 0")

        self.logger = logger or logging.getLogger(__name__)
        self.mac = normalize_mac(mac) if mac else None
        self._host_was_resolved_from_mac = host is None and self.mac is not None
        self.host = host or resolve_host_for_mac(self.mac, logger=self.logger)
        self.password = password or DEFAULT_SWITCH_PASSWORD
        self.http_port = int(http_port)
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.retry_delay = float(retry_delay)
        self.port_index = normalize_port_index(port_index)
        self.warmup = warmup
        self.command_settle_delay = float(command_settle_delay)
        self.verify_timeout = float(verify_timeout)
        self.verify_interval = float(verify_interval)
        self.session_cache_path = self._resolve_session_cache_path(session_cache_path)
        self._session_cache_alias_paths = self._resolve_session_cache_alias_paths(session_cache_path)

        self._cookie: Optional[str] = None
        self._logged_in = False
        self._activation_state: Optional[str] = None
        self._load_cached_cookie()

    @classmethod
    def from_mac(cls, mac: str, password: str, **kwargs: Any) -> "CloudManagedSwtch":
        return cls(password=password, mac=mac, **kwargs)

    @property
    def base_url(self) -> str:
        if self.http_port == 80:
            return f"http://{self.host}"
        return f"http://{self.host}:{self.http_port}"

    def login(self) -> dict[str, Any]:
        activation_state = self.detect_activation_state()
        response = self._request_json(
            LOGIN_COMMAND,
            {"password": self.password},
            authenticate=False,
            warmup=self.warmup,
        )
        login_state = response.get("login")
        if login_state != "success":
            self._logged_in = False
            self._clear_cached_cookie()
            self._cookie = None
            raise SwitchAuthError(f"login failed: {login_state!r}")
        self._logged_in = True
        self._save_cached_cookie()
        if activation_state == ACTIVATION_STATE_NOT_ACTIVE:
            self.logger.info(
                "switch not active, initializing password%s",
                " using default switch password"
                if self.password == DEFAULT_SWITCH_PASSWORD
                else "",
            )
        if self.password == DEFAULT_SWITCH_PASSWORD:
            self.logger.info("login: success using default switch password")
        self.logger.info("login: success")
        return response

    def status(self) -> dict[str, Any]:
        return self._request_json(STATUS_COMMAND)

    def detect_activation_state(self) -> Optional[str]:
        if self._activation_state in {ACTIVATION_STATE_NOT_ACTIVE, ACTIVATION_STATE_ACTIVE}:
            return self._activation_state

        try:
            response = self._request_json(
                ACTIVATION_STATE_COMMAND,
                authenticate=False,
                warmup=self.warmup,
                allow_transport_fallback=False,
                allow_login_fallback=False,
            )
        except SwitchError as exc:
            self.logger.debug("activation-state probe failed: %s", exc)
            return None

        active_state = response.get("Active_state")
        if active_state in {ACTIVATION_STATE_NOT_ACTIVE, ACTIVATION_STATE_ACTIVE}:
            self._activation_state = str(active_state)
            self.logger.debug("detected activation state: %s", self._activation_state)
            return self._activation_state

        return None

    def set_poe(self, port: int, enabled: bool, *, verify: bool = True) -> dict[str, Any]:
        self._validate_port(port)
        opcode = opcode_for_poe(port, enabled, self.port_index)
        response = self._request_json(POE_COMMAND, {"opcode": opcode})

        if response.get("config") != "ok":
            raise SwitchCommandError(f"unexpected PoE config response: {response!r}")

        action = "on" if enabled else "off"
        self.logger.info("poe %s port=%s opcode=%s: ok", action, port, opcode)

        if verify:
            status = self.wait_for_poe_state(port, enabled)
            self.logger.info("final poec: %s", status.get("poec"))
            return status

        return response

    def restart_poe(self, port: int, delay: float = 5.0) -> dict[str, Any]:
        self._validate_port(port)
        if delay < 0:
            raise ValueError("delay must be >= 0")

        off_attempted = False
        on_error: Optional[BaseException] = None

        try:
            off_attempted = True
            self.set_poe(port, False, verify=True)
            self.logger.info("wait: %ss", delay)
            time.sleep(delay)
        finally:
            if off_attempted:
                try:
                    self.set_poe(port, True, verify=False)
                except BaseException as exc:  # pragma: no cover - defensive finally block
                    on_error = exc

        status = self.wait_for_poe_state(port, True)
        self.logger.info("final poec: %s", status.get("poec"))

        if on_error is not None:
            raise SwitchCommandError("PoE ON recovery failed after restart attempt") from on_error

        return status

    def wait_for_poe_state(self, port: int, enabled: bool) -> dict[str, Any]:
        self._validate_port(port)
        expected = 1 if enabled else 0

        if self.command_settle_delay > 0:
            self.logger.debug("settling for %ss before status verification", self.command_settle_delay)
            time.sleep(self.command_settle_delay)

        deadline = time.monotonic() + self.verify_timeout
        last_state: Optional[int] = None
        last_error: Optional[BaseException] = None

        while True:
            try:
                status = self.status()
                actual = poe_state_for_port(status, port, self.port_index)
                last_state = actual
                if actual == expected:
                    return status
                self.logger.warning(
                    "port %s reported state=%s while waiting for state=%s",
                    port,
                    actual,
                    expected,
                )
            except BaseException as exc:
                last_error = exc
                self.logger.warning("status verification failed while waiting for port %s: %s", port, exc)

            if time.monotonic() >= deadline:
                break

            time.sleep(self.verify_interval)

        if last_error is not None:
            raise SwitchCommandError(
                f"port {port} did not reach state {expected} within {self.verify_timeout}s"
            ) from last_error

        raise SwitchCommandError(
            f"port {port} ended in state {last_state}, expected {expected}"
        )

    def _request_json(
        self,
        callcmd: int,
        calldata: Optional[dict[str, Any]] = None,
        *,
        authenticate: bool = True,
        warmup: bool = False,
        allow_transport_fallback: bool = True,
        allow_login_fallback: bool = True,
    ) -> dict[str, Any]:
        last_error: Optional[BaseException] = None

        for attempt in range(1, self.retries + 1):
            try:
                if allow_login_fallback and authenticate and not self._logged_in:
                    try:
                        response = self._send_request(callcmd, calldata, attempt=attempt)
                        data = self._validate_response(response, callcmd)
                        self._logged_in = True
                        self.logger.debug("callcmd=%s succeeded without a fresh login", callcmd)
                        return self._extract_calldata(data)
                    except BaseException as exc:
                        last_error = exc
                        self.logger.debug(
                            "callcmd=%s direct attempt before login failed: %s",
                            callcmd,
                            exc,
                        )
                    self.login()
                if warmup:
                    self._warmup()
                response = self._send_request(
                    callcmd,
                    calldata,
                    attempt=attempt,
                    allow_transport_fallback=allow_transport_fallback,
                )
                data = self._validate_response(response, callcmd)
                return self._extract_calldata(data)
            except BaseException as exc:
                last_error = exc
                if callcmd != LOGIN_COMMAND:
                    self._logged_in = False
                    self._refresh_host_if_needed()
                if attempt >= self.retries:
                    break
                self.logger.warning(
                    "callcmd=%s attempt=%s/%s failed: %s",
                    callcmd,
                    attempt,
                    self.retries,
                    exc,
                )
                time.sleep(self.retry_delay)

        message = f"callcmd {callcmd} failed after {self.retries} attempts"
        raise SwitchTransportError(message) from last_error

    def _send_request(
        self,
        callcmd: int,
        calldata: Optional[dict[str, Any]],
        *,
        attempt: int,
        allow_transport_fallback: bool = True,
    ) -> HttpResponse:
        body = self._encode_body(callcmd, calldata)
        last_error: Optional[BaseException] = None

        transports = [("http.client", self._post_http_client)]
        if allow_transport_fallback:
            transports.append(("raw-socket", self._post_raw_socket))

        for transport_name, transport in transports:
            for profile in HEADER_PROFILES:
                try:
                    effective_profile = profile
                    if callcmd == LOGIN_COMMAND:
                        effective_profile = HeaderProfile(
                            content_type=profile.content_type,
                            include_origin=profile.include_origin,
                            include_x_requested_with=profile.include_x_requested_with,
                            send_cookie=False,
                        )
                    self.logger.debug(
                        "callcmd=%s attempt=%s transport=%s content_type=%s",
                        callcmd,
                        attempt,
                        transport_name,
                        effective_profile.content_type,
                    )
                    response = transport(f"/{callcmd}", body, effective_profile)
                    self._capture_cookie(response.headers)
                    return response
                except BaseException as exc:
                    last_error = exc
                    self.logger.debug(
                        "transport=%s content_type=%s failed: %s",
                        transport_name,
                        profile.content_type,
                        exc,
                    )

        raise SwitchTransportError(
            f"all transports failed for callcmd {callcmd}"
        ) from last_error

    def _encode_body(self, callcmd: int, calldata: Optional[dict[str, Any]]) -> bytes:
        payload: dict[str, Any] = {"data": {"callcmd": callcmd}}
        if calldata is not None:
            payload["data"]["calldata"] = calldata
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def _build_headers(self, content_length: int, profile: HeaderProfile) -> dict[str, str]:
        headers = {
            "Host": self.host if self.http_port == 80 else f"{self.host}:{self.http_port}",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": profile.content_type,
            "Connection": "close",
            "Content-Length": str(content_length),
        }
        if profile.include_x_requested_with:
            headers["X-Requested-With"] = "XMLHttpRequest"
        if profile.include_origin:
            headers["Origin"] = self.base_url
            headers["Referer"] = f"{self.base_url}/"
        if profile.send_cookie and self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    def _post_http_client(self, path: str, body: bytes, profile: HeaderProfile) -> HttpResponse:
        connection = http.client.HTTPConnection(
            self.host,
            self.http_port,
            timeout=self.timeout,
        )
        try:
            connection.request("POST", path, body=body, headers=self._build_headers(len(body), profile))
            response = connection.getresponse()
            data = response.read()
            return HttpResponse(
                status=response.status,
                reason=response.reason,
                headers=tuple(response.getheaders()),
                body=data,
            )
        except OSError as exc:
            raise SwitchTransportError(f"http.client POST {path} failed") from exc
        finally:
            connection.close()

    def _post_raw_socket(self, path: str, body: bytes, profile: HeaderProfile) -> HttpResponse:
        headers = self._build_headers(len(body), profile)
        lines = [f"POST {path} HTTP/1.1"]
        lines.extend(f"{name}: {value}" for name, value in headers.items())
        raw_request = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body

        try:
            with socket.create_connection((self.host, self.http_port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(raw_request)
                chunks: list[bytes] = []
                while True:
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        if chunks:
                            break
                        raise
                    if not chunk:
                        break
                    chunks.append(chunk)
        except OSError as exc:
            raise SwitchTransportError(f"raw socket POST {path} failed") from exc

        return self._parse_raw_http_response(b"".join(chunks))

    def _parse_raw_http_response(self, raw_response: bytes) -> HttpResponse:
        if not raw_response:
            raise SwitchProtocolError("empty response from switch")

        header_sep = b"\r\n\r\n"
        separator = raw_response.find(header_sep)
        if separator < 0:
            raise SwitchProtocolError("raw response missing header separator")

        header_blob = raw_response[:separator].decode("iso-8859-1", errors="replace")
        body = raw_response[separator + len(header_sep) :]
        lines = header_blob.split("\r\n")
        status_line = lines[0]
        parts = status_line.split(" ", 2)
        if len(parts) < 2:
            raise SwitchProtocolError(f"invalid HTTP status line: {status_line!r}")
        try:
            status = int(parts[1])
        except ValueError as exc:
            raise SwitchProtocolError(f"invalid HTTP status code in {status_line!r}") from exc
        reason = parts[2] if len(parts) > 2 else ""

        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers.append((name.strip(), value.strip()))

        return HttpResponse(status=status, reason=reason, headers=tuple(headers), body=body)

    def _validate_response(self, response: HttpResponse, callcmd: int) -> dict[str, Any]:
        if response.status != 200:
            raise SwitchProtocolError(
                f"callcmd {callcmd} returned HTTP {response.status} {response.reason}"
            )
        text = response.body.decode("utf-8", errors="replace").strip()
        if not text:
            raise SwitchProtocolError(f"callcmd {callcmd} returned an empty body")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SwitchProtocolError(f"callcmd {callcmd} returned invalid JSON: {text!r}") from exc
        if not isinstance(data, dict):
            raise SwitchProtocolError(f"callcmd {callcmd} returned a non-object JSON payload")
        if data.get("errcode") != 0:
            raise SwitchCommandError(f"callcmd {callcmd} failed: {data!r}")
        return data

    def _extract_calldata(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SwitchProtocolError("response missing 'data' object")
        calldata = data.get("calldata", {})
        if not isinstance(calldata, dict):
            raise SwitchProtocolError("response missing 'calldata' object")
        return calldata

    def _capture_cookie(self, headers: Iterable[tuple[str, str]]) -> None:
        for name, value in headers:
            if name.lower() != "set-cookie":
                continue
            cookie = value.split(";", 1)[0].strip()
            if cookie:
                self._cookie = cookie
                self._save_cached_cookie()
                self.logger.debug("captured cookie: %s", self._cookie)
                return

    def _warmup(self) -> None:
        connection: Optional[http.client.HTTPConnection] = None
        try:
            connection = http.client.HTTPConnection(
                self.host,
                self.http_port,
                timeout=self.timeout,
            )
            connection.request("GET", "/", headers={"Connection": "close", "Host": self.host})
            response = connection.getresponse()
            response.read()
        except OSError as exc:
            self.logger.debug("warmup GET / failed: %s", exc)
        finally:
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                pass

    def _validate_port(self, port: int) -> None:
        if port < 1 or port > len(self.port_index):
            raise ValueError(f"port must be between 1 and {len(self.port_index)}")

    def _resolve_session_cache_path(
        self, session_cache_path: Optional[os.PathLike[str] | str]
    ) -> Optional[Path]:
        if session_cache_path == "":
            return None
        if session_cache_path is not None:
            return Path(session_cache_path).expanduser()

        return self._default_session_cache_path(self.mac or self.host)

    def _resolve_session_cache_alias_paths(
        self, session_cache_path: Optional[os.PathLike[str] | str]
    ) -> tuple[Path, ...]:
        if session_cache_path is not None:
            return ()
        if not self.mac:
            return ()

        aliases: list[Path] = []
        host_path = self._default_session_cache_path(self.host)
        if host_path != self.session_cache_path:
            aliases.append(host_path)
        return tuple(aliases)

    def _default_session_cache_path(self, cache_key: str) -> Path:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base_dir = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
        safe_key = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in cache_key)
        return base_dir / "cloud-switches-control" / f"{safe_key}.cookie"

    def _load_cached_cookie(self) -> None:
        candidate_paths: list[Path] = []
        if self.session_cache_path is not None:
            candidate_paths.append(self.session_cache_path)
        candidate_paths.extend(path for path in self._session_cache_alias_paths if path not in candidate_paths)

        for path in candidate_paths:
            if not path.exists():
                continue
            try:
                cookie = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                self.logger.debug("failed to read cached cookie from %s: %s", path, exc)
                continue
            if cookie:
                self._cookie = cookie
                self.logger.debug("loaded cached cookie from %s", path)
                return

    def _save_cached_cookie(self) -> None:
        if self.session_cache_path is None or not self._cookie:
            return
        target_paths = [self.session_cache_path, *self._session_cache_alias_paths]
        for path in target_paths:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self._cookie, encoding="utf-8")
            except OSError as exc:
                self.logger.debug("failed to write cached cookie to %s: %s", path, exc)

    def _clear_cached_cookie(self) -> None:
        target_paths: list[Path] = []
        if self.session_cache_path is not None:
            target_paths.append(self.session_cache_path)
        target_paths.extend(path for path in self._session_cache_alias_paths if path not in target_paths)
        for path in target_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                self.logger.debug("failed to delete cached cookie from %s: %s", path, exc)

    def _refresh_host_if_needed(self) -> None:
        if not self._host_was_resolved_from_mac or self.mac is None:
            return
        try:
            resolved_host = resolve_host_for_mac(self.mac, logger=self.logger)
        except SwitchResolutionError as exc:
            self.logger.debug("failed to refresh host from mac %s: %s", self.mac, exc)
            return
        if resolved_host != self.host:
            self.logger.info("switch host changed from %s to %s for mac %s", self.host, resolved_host, self.mac)
            self.host = resolved_host
