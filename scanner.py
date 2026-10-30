#!/usr/bin/env python3
"""PCA10059 RSSI scanner: USB serial acquisition and a localhost waterfall."""

import argparse
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("scanner")


def validate_frame(line):
    """Reject unrelated serial output and incomplete/corrupt measurements."""
    frame = json.loads(line)
    if not isinstance(frame, dict) or frame.get("v") != 1:
        raise ValueError("Unsupported frame version")
    for key in ("seq", "start_us", "sweep_us"):
        if type(frame.get(key)) is not int or not 0 <= frame[key] <= 0xFFFFFFFF:
            raise ValueError(f"Invalid {key}")
    if not 0 < frame["sweep_us"] < 60_000_000:
        raise ValueError("Invalid sweep duration")
    if (frame.get("first_mhz"), frame.get("step_mhz"), frame.get("samples")) != (2400, 1, 16):
        raise ValueError("Unexpected scanner configuration")
    for key in ("mean", "peak"):
        values = frame.get(key)
        if not isinstance(values, list) or len(values) != 81:
            raise ValueError(f"Invalid {key} bin count")
        if any(type(value) is not int or not -127 <= value <= 0 for value in values):
            raise ValueError(f"Invalid {key} RSSI")
    if any(peak < mean for mean, peak in zip(frame["mean"], frame["peak"], strict=True)):
        raise ValueError("Peak RSSI below mean")
    return frame


def discover_port():
    # Match our product string, not every Nordic/CDC device connected to the PC.
    matches = [p.device for p in list_ports.comports() if p.product == "nRF Radio Scanner"]
    if len(matches) > 1:
        raise OSError("Multiple scanners found; select one with --port")
    if not matches:
        raise OSError("Scanner not found. Flash firmware or use --port /dev/ttyACM0")
    return matches[0]


class State:
    def __init__(self):
        self.condition = threading.Condition()
        self.frames = deque(maxlen=600)
        self.counter = 0
        self.status = "Waiting for scanner"
        self.port = None
        self.invalid = 0

    def update_status(self, text, port=None):
        with self.condition:
            changed = self.status != text or self.port != port
            self.status, self.port = text, port
            self.condition.notify_all()
        if changed:
            LOG.info("%s%s", text, f" ({port})" if port else "")

    def publish(self, frame):
        with self.condition:
            self.counter += 1
            frame = {**frame, "id": self.counter, "host_time": time.time()}
            self.frames.append(frame)
            self.status = "Receiving"
            self.condition.notify_all()
        return frame

    def snapshot(self):
        with self.condition:
            return {
                "status": self.status,
                "port": self.port,
                "invalid_frames": self.invalid,
                "frames": list(self.frames),
            }


def acquire(state, stop, port, recording):
    while not stop.is_set():
        try:
            device = port or discover_port()
            state.update_status("Connected, waiting for measurements", device)
            with serial.Serial(device, 115200, timeout=1, exclusive=True) as stream:
                last_valid = time.monotonic()
                while not stop.is_set():
                    line = stream.read_until(b"\n", size=4096)
                    if not line:
                        if time.monotonic() - last_valid > 5:
                            state.update_status("Connected, no recent measurements", device)
                        continue
                    try:
                        if not line.endswith(b"\n"):
                            raise ValueError("Truncated serial line")
                        frame = validate_frame(line)
                    except (ValueError, UnicodeError):
                        with state.condition:
                            state.invalid += 1
                        continue
                    last_valid = time.monotonic()
                    frame = state.publish(frame)
                    if recording:
                        recording.write(json.dumps(frame, separators=(",", ":")) + "\n")
                        recording.flush()
        except (OSError, serial.SerialException) as error:
            state.update_status(str(error))
            stop.wait(1)


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            LOG.debug(fmt, *args)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/events":
                self.events()
                return
            if path == "/api/state":
                data = json.dumps(state.snapshot(), separators=(",", ":")).encode()
                content_type = "application/json"
            elif path in ("/", "/index.html", "/app.js", "/style.css"):
                name = "index.html" if path == "/" else path[1:]
                data = (ROOT / "web" / name).read_bytes()
                content_type = {"html": "text/html", "js": "text/javascript", "css": "text/css"}[
                    name.rsplit(".", 1)[1]
                ]
            elif path == "/capture.jsonl":
                frames = state.snapshot()["frames"]
                data = "".join(json.dumps(frame) + "\n" for frame in frames).encode()
                content_type = "application/x-ndjson"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                last_id = max(0, int(self.headers.get("Last-Event-ID", "0")))
            except ValueError:
                last_id = 0
            try:
                while True:
                    with state.condition:
                        if state.counter <= last_id:
                            state.condition.wait(timeout=1)
                        frames = [frame for frame in state.frames if frame["id"] > last_id]
                        status = {
                            "status": state.status,
                            "port": state.port,
                            "invalid_frames": state.invalid,
                        }
                    for frame in frames:
                        payload = json.dumps(frame, separators=(",", ":"))
                        self.wfile.write(f"id: {frame['id']}\ndata: {payload}\n\n".encode())
                        last_id = frame["id"]
                    self.wfile.write(f"event: status\ndata: {json.dumps(status)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="USB CDC port; default: auto-detect scanner product")
    parser.add_argument("--http-port", type=int, default=8765)
    parser.add_argument("--record", type=Path, help="Append all sweeps to a JSONL file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    state = State()
    stop = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", args.http_port), make_handler(state))
    recording = None
    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        recording = args.record.open("a", encoding="utf-8")
        LOG.info("Recording to %s (UTC %s)", args.record, datetime.now(timezone.utc).isoformat())
    thread = threading.Thread(target=acquire, args=(state, stop, args.port, recording), daemon=True)
    thread.start()
    LOG.info("Open http://127.0.0.1:%s", args.http_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        thread.join(timeout=3)
        if recording:
            recording.close()


if __name__ == "__main__":
    main()
