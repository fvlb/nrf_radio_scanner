#!/usr/bin/env python3
"""Flash only the scanner application using the PCA10059's existing USB DFU bootloader."""

import argparse
import subprocess
import time
from pathlib import Path

from serial.tools import list_ports

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=int, default=120, help="Seconds to wait for RESET button")
    args = parser.parse_args()
    package = ROOT / "build/scanner.zip"
    tool = ROOT / ".deps/nrfutil"
    if not package.is_file() or not tool.is_file():
        parser.error("Run bash tools/setup.sh and python3 tools/build.py first")
    print("Press PCA10059 RESET: red LED should pulse. Waiting for USB 1915:521f…", flush=True)
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        ports = [p for p in list_ports.comports() if (p.vid, p.pid) == (0x1915, 0x521F)]
        if len(ports) > 1:
            parser.error("Multiple Nordic DFU devices; disconnect the other DFU dongles")
        if ports:
            port = ports[0].device
            print(f"Flashing application-only package through {port}", flush=True)
            subprocess.run(
                [str(tool), "dfu", "usb-serial", "-pkg", str(package), "-p", port],
                check=True,
                timeout=120,
            )
            return
        time.sleep(0.5)
    parser.exit(1, "Bootloader did not appear. Press RESET and retry; no firmware was changed.\n")


if __name__ == "__main__":
    main()
