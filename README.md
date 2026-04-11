# Home Assistant ET312 Integration

This is a combination of two things;
1) A piece of python code which will run on a Raspberry Pi, connect to an ET312 via serial and an MQTT server via TCP/IP. It will publish status to the MQTT server and optionally consume messages from the MQTT server and control the ET312.
2) A Home Asssistant module that can be installed via HACS into Home Assistant and control the ET312, via the MQTT as a go-between (so the Home Assistant server and the Raspberry Pi/ET312 can be far apart.

I totally just vibe coded the crap out of this with ChatGPT and Codex, so don't blame me if it zaps you in the balls or does something else weird, it seemed to work for me, YMMV.

## Versioning

This repository uses two version sources on purpose:

- `custom_components/et312/manifest.json` `version` is the version Home Assistant shows after install.
- GitHub Releases are the version HACS shows as the available remote update.

To keep Home Assistant and HACS aligned, use this release pattern:

1. Bump `custom_components/et312/manifest.json` to the next SemVer version.
2. Merge that change to `main`.
3. Create a GitHub Release with the matching tag, prefixed with `v`.

Example:

- `manifest.json`: `0.4.3`
- GitHub release tag: `v0.4.3`

If you skip GitHub Releases, Home Assistant will still show the manifest version,
but HACS will typically fall back to showing a commit-based version instead of a
clean SemVer release number.

The repository includes three GitHub Actions to keep this tidy:

- HACS validation
- Hassfest validation
- a version-metadata check that makes sure `vX.Y.Z` tags match `manifest.json`

## Current status

This repository currently contains the Home Assistant-side scaffold and a
protocol model derived from some existing projects:

- [Carumbad/et312_mqtt](https://github.com/Carumbad/et312_mqtt)
- [fenbyfluid/three-twelve-bee](https://github.com/fenbyfluid/three-twelve-bee)

The integration is now aligned around a transport-agnostic ET312 client:

- Config flow for choosing either direct serial or MQTT bridge
- Polling data coordinator
- Sensor entities for mode, channel power levels, battery, and MA value
- Control entities for routine selection, channel A/B power setpoints, MA, and front-panel control lockout
- ET312 packet helpers for checksum, XOR cipher, register reads, and writes
- Home Assistant MQTT bridge support plus the direct serial path

## Protocol notes

The references agree on the core serial protocol:

- Serial speed is `19200` baud by default
- Sync by sending `0x00` until the device responds with `0x07`
- Negotiate a cipher key with `0x2F 0x00`, then XOR bytes with `device_key ^ 0x55`
- Read memory with opcode `0x3C`
- Write memory with opcode `0x3D + (len(data) << 4)`

Useful registers for state polling:

- `0x407B`: current mode
- `0x4064`: channel A level
- `0x4065`: channel B level
- `0x4203`: battery percent
- `0x420D`: multi-adjust value

## Architecture direction

The Home Assistant-side model should stay shared while we support two
deployment styles:

- `serial`: the ET312 is plugged directly into the Home Assistant host
- `mqtt`: a remote Python bridge handles serial and exposes the device over MQTT

On the Raspberry Pi bridge host, the `0.5.x` refactor moves toward a
multi-device layout:

- one shared install under `/opt/et312-mqtt-bridge`
- one shared bridge config and one shared discovery config
- one per-device config file under `/opt/et312-mqtt-bridge/config/devices/`
- one RFCOMM unit and one MQTT bridge unit per discovered ET312

Bluetooth-backed device ids use the last 6 hex characters of the chosen MAC,
for example `ET312_7D4FFB`. Discovery also de-duplicates alias Bluetooth
identities that appear to represent the same physical ET312, so one box should
not be registered twice just because BlueZ exposes more than one nearby `Micro`
identity for it.

For the `serial` path, the integration assumes the user provides a working
serial device path such as `/dev/ttyUSB0`, `/dev/ttyACM0`, or a Bluetooth-backed
`/dev/tty*` device exposed by the host OS.

For the `mqtt` path, Home Assistant should already have its MQTT integration
configured. The ET312 integration subscribes to bridge topics, publishes
commands through Home Assistant's MQTT integration, and never opens the device
directly.

## MQTT Bridge Contract

The bridge publishes retained state JSON to a state topic. In a multi-device Pi
install, the default topic layout is per-device, for example:

- `et312/ET312_8EE738/state`
- `et312/ET312_8EE738/command`
- `et312/ET312_8EE738/availability`

State payload example:

```json
{
  "connected": true,
  "device_id": "ET312_8EE738",
  "mode_code": 118,
  "mode": "Waves",
  "power_level_a": 10,
  "power_level_b": 12,
  "battery_percent": 72,
  "multi_adjust": 50,
  "front_panel_controls_disabled": true
}
```

`power_level_a` and `power_level_b` are integer ET312 output levels from `0`
to `99`. `multi_adjust` is a `0` to `100` percentage mapped from the
current mode's ET312 multi-adjust range (`$4086` minimum, `$4087` maximum)
and live value (`$420D`). The bridge caches that range per mode and refreshes
it when the mode changes. The raw upper bound maps to the bottom of the
ET312 front-panel dial.

It publishes availability to the matching availability topic using `online` and
`offline`.

Home Assistant publishes JSON commands to the matching command topic:

```json
{"command": "set_mode", "mode": "Waves"}
{"command": "set_power", "channel": "a", "value": 10}
{"command": "set_power", "channel": "b", "value": 12}
{"command": "set_multi_adjust", "value": 50}
{"command": "set_front_panel_controls_disabled", "value": true}
{"command": "request_state"}
```

## Testing

Unit tests for the core client live in `tests/test_et312_client.py`:

```bash
python3 -m unittest tests.test_et312_client
```

For a minimal live hardware smoke test against a real ET312, use:

```bash
python3 scripts/live_serial_smoke_test.py /dev/ttyUSB0 --read-only
python3 scripts/live_serial_smoke_test.py /dev/ttyUSB0 --mode Waves --power-a 10 --power-b 10 --ma 50
```

The smoke test connects, prints the initial state, optionally changes the mode
and channel power levels, then reads the state again.

If the ET312 is not syncing reliably, a lower-level probe is available:

```bash
python3 scripts/probe_serial_sync.py /dev/cu.Micro312-Audio
```

That script sends raw `0x00` sync bytes at both `19200` and `38400` baud and
prints any response bytes, which is useful for debugging Bluetooth serial links.

## MQTT Bridge Script

The MQTT bridge process lives at `scripts/et312_mqtt_bridge.py`.

Install its Python dependencies on the bridge host:

```bash
python3 -m pip install pyserial paho-mqtt
```

Example:

```bash
python3 scripts/et312_mqtt_bridge.py /dev/ttyUSB0 --mqtt-host 127.0.0.1
```

The bridge:

- opens the ET312 over serial
- syncs and negotiates the ET312 cipher key
- publishes retained JSON state at startup, on `request_state`, and when values change
- repeats changed state once per second for three seconds, then stays quiet until another change
- publishes `online` and `offline` to the availability topic
- accepts `set_mode`, `set_power`, `set_multi_adjust`, `set_front_panel_controls_disabled`, and `request_state` JSON commands
- uses slower, retry-heavy sync defaults that are friendlier to Bluetooth RFCOMM links

## Raspberry Pi Install

A Raspberry Pi 4 running Raspberry Pi OS is a sensible host for the MQTT bridge.
The bridge is lightweight, and the Pi gives you a stable always-on serial and
MQTT endpoint near the ET312.

From a fresh Raspberry Pi OS install:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/Carumbad/HomeAssistant_ET312.git
cd HomeAssistant_ET312
sudo ./scripts/install_rpi_bridge.sh --mqtt-host 192.168.1.20
```

The installer:

- installs Python and bridge dependencies
- copies this project into `/opt/et312-mqtt-bridge`
- creates an `et312` system user
- grants that user access to `dialout`
- writes shared bridge settings to `/opt/et312-mqtt-bridge/config/et312-bridge.env`
- writes shared discovery settings to `/opt/et312-mqtt-bridge/config/et312-discovery.env`
- prepares `/opt/et312-mqtt-bridge/config/devices/` for per-device configs

If you want to register a directly attached serial device immediately, you can
still do that during install:

```bash
sudo ./scripts/install_rpi_bridge.sh --mqtt-host 192.168.1.20 --device /dev/ttyUSB0
```

If you rerun the bridge installer, it reuses the existing virtualenv and only
downloads Python packages when `pyserial` or `paho-mqtt` are missing. It also
preserves the existing `/opt/et312-mqtt-bridge/config/` files so the Bluetooth
RFCOMM settings are not overwritten by the bridge install step.

After install, useful commands are:

```bash
sudo systemctl list-units 'et312-*'
sudo journalctl -u 'et312-rfcomm-*' -u 'et312-mqtt-bridge-*' -f
sudo editor /opt/et312-mqtt-bridge/config/et312-bridge.env
sudo editor /opt/et312-mqtt-bridge/config/et312-discovery.env
sudo ls /opt/et312-mqtt-bridge/config/devices
```

For routine bridge updates on the Pi, use:

```bash
cd ~/HomeAssistant_ET312
sudo ./scripts/update_rpi_bridge.sh
```

That updater pulls the latest checked-out branch, refreshes
`/opt/et312-mqtt-bridge`, preserves the existing config files, regenerates the
per-device units, and cleanly restarts all configured ET312 instances.

If you want the updater to run Bluetooth discovery before restarting units:

```bash
cd ~/HomeAssistant_ET312
sudo ./scripts/update_rpi_bridge.sh --discover
```

## Raspberry Pi Bluetooth Serial Setup

If the ET312 will connect to the Pi over Bluetooth instead of USB serial, there
is a separate helper script for the Bluetooth stack, discovery, and RFCOMM
mapping:

```bash
sudo ./scripts/install_rpi_bluetooth_serial.sh --discover
```

Important:

- discovery starts by scanning for Bluetooth names that match the shared
  discovery fragments, currently `Micro,312`
- discovery then interrogates each candidate over a temporary RFCOMM link and
  only saves devices that actually answer like an ET312
- if one physical ET312 exposes more than one nearby Bluetooth identity,
  discovery keeps only one saved device entry for that ET312 id
- the Pi scripts now assume ET312 devices use RFCOMM channel `2`
- SDP is only used as a sanity check during discovery/debugging

Discovery creates or refreshes per-device env files under:

```text
/opt/et312-mqtt-bridge/config/devices/ET312_XXXXXX.env
```

Each saved Bluetooth device then gets:

- one RFCOMM unit such as `et312-rfcomm-ET312_8EE738.service`
- one bridge unit such as `et312-mqtt-bridge-ET312_8EE738.service`

If you already know a Bluetooth MAC and want to register that device directly:

```bash
sudo ./scripts/install_rpi_bluetooth_serial.sh --mac AA:BB:CC:DD:EE:FF
```

Useful Bluetooth commands afterward:

```bash
sudo systemctl list-units 'et312-*'
sudo journalctl -u 'et312-rfcomm-*' -u 'et312-mqtt-bridge-*' -f
sudo rfcomm
ls -l /dev/rfcomm*
```

For Bluetooth serial, the bridge installer defaults are intentionally more
patient than the wired case: longer startup delay, more sync attempts, and
reconnect retries before giving up.

For Home Assistant, install the integration with HACS, restart Home Assistant,
then add the `ET312` integration from Settings -> Devices & services. Choose
the MQTT connection type and enter the shared topic prefix, usually `et312`.
The integration subscribes to `et312/+/state` and `et312/+/availability`, then
creates one Home Assistant device and entity set for each discovered device id
such as `ET312_8EE738`.
