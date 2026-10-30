import copy
import json
import os
import pty
import select
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scanner import State, acquire, make_handler, validate_config, validate_frame
from tools.build import verify_hex


def example():
    return {
        "v": 1,
        "seq": 42,
        "start_us": 0xFFFFFF00,
        "sweep_us": 190000,
        "first_mhz": 2400,
        "step_mhz": 1,
        "samples": 16,
        "mean": [-95] * 81,
        "peak": [-60] * 81,
    }


class ProtocolTests(unittest.TestCase):
    def test_timing_limits_and_invalid_values(self):
        for samples, gap, pause in [(1, 0, 0), (64, 2000, 5000), (4, 16, 100)]:
            config = {"samples": samples, "sample_interval_us": gap, "pause_ms": pause}
            self.assertEqual(validate_config(config), config)
            frame = {**example(), **config}
            self.assertEqual(validate_frame(json.dumps(frame)), frame)
        default = {"samples": 16, "sample_interval_us": 128, "pause_ms": 0}
        for key, value in [
            ("samples", 0),
            ("samples", 65),
            ("samples", True),
            ("sample_interval_us", -1),
            ("sample_interval_us", 2001),
            ("pause_ms", 5001),
            ("pause_ms", "100"),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_config({**default, key: value})
        with self.assertRaises(ValueError):
            validate_config({**default, "unexpected": 1})

    def test_image_validation_preserves_bootloader_and_uicr(self):
        def record(kind, address, payload):
            data = bytes([len(payload), address >> 8, address & 255, kind]) + payload
            return ":" + (data + bytes([-sum(data) & 255])).hex()

        eof = record(1, 0, b"")
        self.assertEqual(verify_hex(record(0, 0x1000, b"app") + "\n" + eof), 3)
        for kind in (3, 5):
            self.assertEqual(
                verify_hex(
                    "\n".join(
                        [record(0, 0x1000, b"app"), record(kind, 0, b"\x00\x00\x12\xbd"), eof]
                    )
                ),
                3,
            )
        for base, address in [(0, 0), (0xE, 0), (0x1000, 0x1000)]:
            image = "\n".join(
                [record(4, 0, base.to_bytes(2, "big")), record(0, address, b"bad"), eof]
            )
            with self.subTest(base=base, address=address), self.assertRaises(ValueError):
                verify_hex(image)
        with self.assertRaises(ValueError):
            verify_hex(":0310000061707000\n" + eof)  # Incorrect checksum.

    def test_accepts_noise_floor_and_strong_signal(self):
        frame = example()
        frame["mean"][0], frame["peak"][0] = -127, 0
        self.assertEqual(validate_frame(json.dumps(frame)), frame)

    def test_rejects_unrelated_and_corrupt_frames(self):
        candidates = [
            [],
            {},
            {**example(), "v": 2},
            {**example(), "seq": True},
            {**example(), "sweep_us": 0},
            {**example(), "first_mhz": 2402},
        ]
        for key, values in [
            ("mean", [-90] * 40),
            ("peak", [-128] * 81),
            ("mean", [-10] * 81),
            ("mean", ["-95"] * 81),
        ]:
            frame = copy.deepcopy(example())
            frame[key] = values
            candidates.append(frame)
        for frame in candidates:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                validate_frame(json.dumps(frame))

    def test_history_is_bounded_and_host_ids_survive_device_sequence_reset(self):
        state = State()
        for sequence in range(605):
            state.publish({**example(), "seq": sequence % 10})
        snapshot = state.snapshot()
        self.assertEqual(len(snapshot["frames"]), 600)
        self.assertEqual(snapshot["frames"][0]["id"], 6)
        self.assertEqual(snapshot["frames"][-1]["id"], 605)


class IntegrationTests(unittest.TestCase):
    def test_serial_acquisition_http_and_event_stream(self):
        master, slave = pty.openpty()
        state, stop = State(), threading.Event()
        acquisition = threading.Thread(
            target=acquire, args=(state, stop, os.ttyname(slave), None), daemon=True
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
        http_thread = threading.Thread(target=server.serve_forever, daemon=True)
        acquisition.start()
        http_thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            time.sleep(0.1)  # Let pyserial configure the PTY before sending bytes.
            os.write(master, b"not a scanner\n")
            os.write(master, (json.dumps(example()) + "\n").encode())
            deadline = time.monotonic() + 3
            while state.counter == 0 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(state.counter, 1)
            with urlopen(base + "/api/state", timeout=3) as response:
                snapshot = json.load(response)
            self.assertEqual(snapshot["frames"][0]["seq"], 42)
            self.assertEqual(snapshot["invalid_frames"], 1)
            with urlopen(base + "/events", timeout=3) as response:
                self.assertEqual(response.readline(), b"id: 1\n")
                self.assertEqual(json.loads(response.readline()[6:])["seq"], 42)
            with urlopen(base + "/", timeout=3) as response:
                self.assertIn(b"Waterfall", response.read())
            with urlopen(base + "/capture.jsonl", timeout=3) as response:
                self.assertEqual(json.loads(response.readline())["seq"], 42)

            config = {"samples": 4, "sample_interval_us": 16, "pause_ms": 100}
            request = Request(
                base + "/api/config",
                data=json.dumps(config).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 202)
            self.assertTrue(state.snapshot()["config_pending"])
            self.assertTrue(select.select([master], [], [], 3)[0])
            self.assertEqual(os.read(master, 1024), b"C 4 16 100\n")
            os.write(master, (json.dumps({**example(), **config, "seq": 43}) + "\n").encode())
            deadline = time.monotonic() + 3
            while state.counter < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(state.snapshot()["applied_config"], config)
            self.assertFalse(state.snapshot()["config_pending"])
            invalid = Request(
                base + "/api/config",
                data=b'{"samples":0}',
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(invalid, timeout=3)
            self.assertEqual(rejected.exception.code, 400)
            self.assertEqual(state.snapshot()["requested_config"], config)
        finally:
            stop.set()
            server.shutdown()
            server.server_close()
            acquisition.join(timeout=3)
            http_thread.join(timeout=3)
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
