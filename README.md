# nRF Radio Scanner — PCA10059 USB dongle

A receive-only 2.4 GHz **swept RSSI energy detector** with a local browser
waterfall. Detects energy without needing valid BLE packets. This is **not**
a HackRF-style I/Q SDR or a simultaneous full-band capture.

**Bring-up status (2026-10-30):** firmware builds and the serial/web integration
checks pass. The initial image was accepted by the real dongle's USB DFU
bootloader, but USB enumeration then timed out. A revised USB-startup image is
built and awaits a physical RESET/reflash. Live RF measurements are not yet
verified. The web server displays an explicit waiting/disconnected status;
it does not substitute simulated data for measurements.

## See the waterfall

```sh
make setup
make run
```

Open **http://127.0.0.1:8765** on the same PC. The dongle must have the scanner
firmware installed (see below). The page has a live spectrum trace, maximum
hold, scrolling waterfall, peak/mean selection, adjustable color scale,
frequency inspection on hover, BLE advertising markers and Wi-Fi 1/6/11 guides.
There are no external web assets or CDN dependencies.

**Connection:** dongle → USB CDC serial → Python/pyserial → localhost HTTP/SSE →
browser. It does not use WebUSB or Web Serial. The server binds to localhost
only. Closing the browser does not stop recording; Ctrl+C in the server terminal
stops acquisition. The service reconnects automatically after unplug/replug.

`make run` records all sweeps in `captures/session.jsonl`. The page's download
link exports the most recent 600 sweeps. To choose a port explicitly:

```sh
.venv/bin/python scanner.py --port /dev/ttyACM0 --record captures/experiment.jsonl
```

## Hardware and measurements

- Nordic **PCA10059 nRF52840 dongle**, stock Nordic USB DFU bootloader.
- 81 tuning points: **2400 through 2480 MHz inclusive**, in 1 MHz steps.
- BLE 1 Mbit/s receiver mode, raw RSSI tasks; no BLE stack, packet requirement,
  transmissions, scan requests or automatic acknowledgements.
- 16 RSSI measurements per point, with a 128 µs delay after each measurement.
  Receiver ramp-up and a 40 µs settling delay precede sampling each point.
- Reports arithmetic mean of dBm readings and strongest sampled RSSI per point.
  `sweep_us` measures the actual radio sweep duration, excluding USB transfer time.
- Green LED toggles per sweep. USB acquisition starts when the serial port opens.

Each point is observed at a **different time**, lowest frequency first. Short
bursts can be missed. The receiver filter limits frequency resolution; a 1 MHz
tuning step does not establish a calibrated 1 MHz resolution bandwidth.
Adjacent bins may respond to the same signal. RSSI near the noise floor is
limited and is not a calibrated spectrum-analyzer noise measurement.

BLE logical channel mapping:

| Channel | Center frequency |
|---|---|
| Advertising 37 | 2402 MHz |
| Data 0–10 | 2404–2424 MHz, 2 MHz steps |
| Advertising 38 | 2426 MHz |
| Data 11–36 | 2428–2478 MHz, 2 MHz steps |
| Advertising 39 | 2480 MHz |

## Build and flash on Linux x86-64

Prerequisites: Python **3.10+** with venv support, `make`, Git, curl, tar, and
access to the dongle's `/dev/ttyACM*` port. `make setup` downloads a pinned nRF5
SDK, xPack ARM GCC **10.3.1-2.3**, and Nordic **nrfutil 6.1.7** into `.deps/`;
Python dependencies go into `.venv/`. Nothing is installed system-wide.

```sh
make setup
make build
make flash
```

When prompted, press the dongle's **sideways RESET button**, not the large user
button. Its red LED should pulse, and USB DFU appears as **1915:521f**.
The flashing script waits up to 120 seconds for exactly one such device.

The package contains **only an application**, starting at **0x1000** and bounded
below **0xE0000**. MBR, bootloader, bootloader settings and UICR are not included.
The build validates every Intel HEX data record against that address range.
The existing application is overwritten; it is not backed up.
No external debug probe is needed. Outputs: `build/scanner.hex`, `build/scanner.zip`.

After installation, hold the large **user button for one second**, or send `b`
over the scanner serial port, to request the stock Nordic bootloader again.
The physical RESET button remains the fallback. Stop the acquisition service
before using the serial port with another tool.

## Investigation procedure

1. Place the dongle near the BLE receiver that experiences trouble.
2. Record a baseline with nearby equipment operating normally.
3. Switch suspected devices off and on individually. Look for repeatable changes
   at the same frequencies; compare mean and peak views.
4. Move the dongle toward suspected sources and compare relative levels.
5. Check PC/USB noise too: move away from USB 3 equipment using a USB extension.

Broad energy across many bins is consistent with Wi-Fi; a stable narrow region
can indicate a spur. These shapes are clues, not unique source identification.

## Serial protocol

One JSON object per line:

```json
{"v":1,"seq":42,"start_us":12345,"sweep_us":190000,"first_mhz":2400,"step_mhz":1,"samples":16,"mean":[-95],"peak":[-60]}
```

The arrays above are abbreviated: actual frames contain **81 integer dBm values
in each array**. `start_us` is a wrapping 32-bit device uptime timestamp;
`seq` is a wrapping 32-bit sweep sequence. The host adds an independent `id`
and wall-clock `host_time` in seconds. Malformed frames are counted and rejected.

## Checks

```sh
make check
make build
```

Checks include Ruff formatting/lint, protocol validation and real PTY → serial
reader → HTTP/SSE integration tests. Firmware builds with `-Wall -Werror`.
Tests do not require a dongle. Dependencies/build outputs/captures are gitignored.

Third-party license information: [THIRD_PARTY.md](THIRD_PARTY.md).

## Optional background service on this desktop

```sh
systemd-run --user --unit=nrf-radio-scanner \
  --working-directory="$PWD" \
  "$PWD/.venv/bin/python" scanner.py --record captures/session.jsonl
xdg-open http://127.0.0.1:8765
```

Stop with `systemctl --user stop nrf-radio-scanner`; inspect logs with
`journalctl --user -u nrf-radio-scanner`. This transient service does not start
automatically after reboot. To run it again after stopping, use `make run`.
