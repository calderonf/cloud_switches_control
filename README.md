# cloud-switches-control

Headless PoE control for CloudManagedSwtch devices from Linux, Orange Pi, or any other machine without a GUI.

The main use case is restarting PoE on one physical port when a camera hangs, without rebooting the whole switch and without depending on Chrome, a mobile app, or the web UI.

## Status

This project is tailored to CloudManagedSwtch devices that expose:

- `POST /123` for login
- `POST /101` for status
- `POST /103` for PoE configuration

It defaults to the 4-PoE-port mapping observed on the tested switch:

- Firmware: `6.0.250516`
- Model/UI: `2FE.POE+2GE.POE+1GE`
- HTTP server: `lwIP/1.3.1`
- Default `portIndex`: `[3, 2, 1, 0]`

## Installation

Local editable install:

```bash
python3 -m pip install -e .
```

Install test dependencies too:

```bash
python3 -m pip install -e '.[test]'
```

## CLI Usage

You can identify the switch with either:

- `--host 192.168.x.x`
- `--mac 5C15C5088EDA`
- the short positional form: `switch-poe 192.168.0.220 status`

If you do not provide `--password` and `SWITCH_PASSWORD` is not set, the tool falls back to the factory default password `123456`.

Optional environment variables:

```bash
export SWITCH_PASSWORD='your-password'
export SWITCH_SESSION_CACHE='/tmp/cloud-managed-switch.cookie'
```

### Read Full Status

By IP:

```bash
switch-poe --host 192.168.0.220 status
switch-poe 192.168.0.220 status
```

By MAC:

```bash
switch-poe --mac 5C15C5088EDA status
switch-poe 5C15C5088EDA status
```

### Read Specific Status Fields

Return only TX counters:

```bash
switch-poe 192.168.0.220 status --tx
```

Example output:

```json
[
  "0",
  "0",
  "56",
  "0",
  "40"
]
```

Other field selectors available on `status`:

```bash
switch-poe 192.168.0.220 status --rx
switch-poe 192.168.0.220 status --poec
switch-poe 192.168.0.220 status --link
switch-poe 192.168.0.220 status --pw
switch-poe 192.168.0.220 status --tp
switch-poe 192.168.0.220 status --vol
```

By default the CLI stays quiet and prints only the requested JSON result when the command succeeds. If you want informative logs such as MAC resolution, first-time activation detection, or login success, add `-v`:

```bash
switch-poe -v 5C15C5088EDA status
switch-poe --verbose 192.168.0.220 restart --port 1 --delay 5
```

### Power Off One Port

Turn PoE off on physical port 1:

```bash
switch-poe 192.168.0.220 poe --port 1 --off
```

By MAC:

```bash
switch-poe 5C15C5088EDA poe --port 1 --off
```

### Power On One Port

Turn PoE on on physical port 1:

```bash
switch-poe 192.168.0.220 poe --port 1 --on
```

By MAC:

```bash
switch-poe 5C15C5088EDA poe --port 1 --on
```

### Restart One Port

Power-cycle physical port 1 with a 5-second delay:

```bash
switch-poe 192.168.0.220 restart --port 1 --delay 5
```

By MAC:

```bash
switch-poe 5C15C5088EDA restart --port 1 --delay 5
```

Restart camera power on physical port 2:

```bash
switch-poe 5C15C5088EDA restart --port 2 --delay 5
```

### Operational Notes

- `status` prints JSON to stdout.
- `poe` and `restart` also print JSON with the final status payload.
- The CLI is non-interactive by default and does not ask for confirmation.
- If no password is provided, the CLI uses the factory default `123456`.
- Success logs are quiet by default. Use `-v` or `--verbose` to print informative logs to stderr.
- Warnings and errors still print by default.
- After a PoE change, the tool waits briefly and polls `/101` because some switches apply the change before they report the new state.
- On the tested model, `poec` appears to be reported in internal index order, so port verification is mapped through `portIndex`.
- MAC lookup uses the local Linux neighbor table (`ip neigh`, `/proc/net/arp`, `arp -an`).
- If the controlling device is physically connected to port 1, turning off or restarting port 1 can cut your own connectivity to the switch.

### Useful Runtime Flags

```bash
switch-poe 192.168.0.220 --timeout 3 --retries 5 --retry-delay 1 restart --port 1 --delay 5
switch-poe 5C15C5088EDA restart --port 2 --delay 5
switch-poe 192.168.0.220 --port-index 3,2,1,0 status
switch-poe -v 192.168.0.220 status
switch-poe 192.168.0.220 --log-level DEBUG status
switch-poe 192.168.0.220 --command-settle-delay 2 --verify-timeout 30 status
switch-poe 192.168.0.220 --session-cache-path /tmp/cloud-managed-switch.cookie poe --port 1 --on
```

## Python Usage

```python
from cloud_switches_control import CloudManagedSwtch

sw = CloudManagedSwtch("192.168.0.220", password="your-password")
print(sw.status())
sw.restart_poe(port=1, delay=5)
```

If you omit `password`, the Python client also falls back to `123456`:

```python
from cloud_switches_control import CloudManagedSwtch

sw = CloudManagedSwtch("192.168.0.220")
print(sw.status())
```

Resolve the switch by MAC instead of hardcoding the IP:

```python
from cloud_switches_control import CloudManagedSwtch

sw = CloudManagedSwtch.from_mac("5C15C5088EDA", password="your-password")
print(sw.status())
sw.restart_poe(port=2, delay=5)
```

Turn PoE on or off explicitly:

```python
sw.set_poe(port=1, enabled=False)
sw.set_poe(port=1, enabled=True)
```

Read only the TX counters from the status payload:

```python
status = sw.status()
print(status["tx"])
```

## Environment Variables

- `SWITCH_PASSWORD`: default password used by the CLI if `--password` is omitted.
- `SWITCH_SESSION_CACHE`: optional path for the persisted session cookie.

## First-Time Activation

On a factory-reset switch, the web UI can show an initial-password screen instead of the normal login. The page logic checks `callcmd: 100` for `Active_state`, but it still submits the chosen password through the normal login endpoint:

```json
{"data":{"callcmd":123,"calldata":{"password":"123456"}}}
```

The confirm-password field is only browser-side validation. The switch itself receives a single password value. Because of that, this CLI can reuse the same `callcmd: 123` flow for first-time activation, and its default fallback password is `123456` unless you override it.

The client now probes `callcmd: 100` before login when possible, so logs can explicitly tell you when the switch is still in first-time activation mode.

## Opcode Table

For the tested 4-port PoE model, the observed UI mapping is:

| Physical port | Internal index | PoE OFF opcode | PoE ON opcode |
| --- | ---: | ---: | ---: |
| 1 | 3 | 50 | 562 |
| 2 | 2 | 34 | 546 |
| 3 | 1 | 18 | 530 |
| 4 | 0 | 2 | 514 |

Formula:

```text
opcode = (value << 9) | (internal_index << 4) | 2
```

Where:

- `value = 0` means PoE OFF
- `value = 1` means PoE ON
- `internal_index = portIndex[physical_port - 1]`

## Notes About the `lwIP` HTTP Bug

The embedded server has shown inconsistent behavior with normal CLI clients:

- `curl` may succeed once and then fail with `Empty reply from server`
- `requests` can fail with `RemoteDisconnected`
- the browser UI works more reliably than naive HTTP scripts

To improve compatibility, this library:

- sends browser-like headers
- uses `Connection: close`
- optionally warms up the switch with `GET /`
- preserves any cookie returned by the login response
- reuses the session cookie across separate CLI invocations by default
- can resolve the switch IP from its MAC using the local ARP/neighbor table
- retries requests with short sleeps
- falls back from `http.client` to a raw TCP socket HTTP implementation
- can try operational endpoints before forcing a fresh login when the login endpoint is unstable
- validates JSON and `errcode == 0` on every response
- verifies the final PoE state after `poe` and `restart` with polling instead of a single immediate check

## Project Layout

```text
cloud_switches_control/
  pyproject.toml
  src/cloud_switches_control/
    __init__.py
    cloud_managed_switch.py
    cli.py
  tests/
    test_opcodes.py
  README.md
```

## Running Tests

```bash
pytest
```
