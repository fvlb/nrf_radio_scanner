#!/usr/bin/env python3
"""Build an application-only PCA10059 image using pinned nRF5 SDK inputs."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK_REV = "bd4841e2e9b459b1ac45781807db5cedad2ce633"


def verify_hex(text):
    """Reject data outside the application region, including MBR/UICR/bootloader."""
    base, total, eof = 0, 0, False
    for line in text.splitlines():
        if not line.startswith(":") or eof:
            raise ValueError("Invalid Intel HEX record")
        record = bytes.fromhex(line[1:])
        if len(record) < 5 or len(record) != record[0] + 5 or sum(record) % 256:
            raise ValueError("Invalid Intel HEX length/checksum")
        count, kind = record[0], record[3]
        address = base + int.from_bytes(record[1:3], "big")
        if kind == 0:
            if not 0x1000 <= address < address + count <= 0xE0000:
                raise ValueError(f"Data outside application flash: {address:#x}")
            total += count
        elif kind == 4 and count == 2:
            base = int.from_bytes(record[4:6], "big") << 16
        elif kind == 1 and count == 0:
            eof = True
        elif kind not in {3, 5} or count != 4:
            raise ValueError("Unsupported Intel HEX record")
    if not eof or not total:
        raise ValueError("Empty or incomplete application image")
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    sdk = Path(os.environ.get("NRF5_SDK", ROOT / ".deps/nrf5-sdk")).resolve()
    gcc = Path(os.environ.get("ARM_GCC", ROOT / ".deps/arm-gcc/bin")).resolve()
    if not (gcc / "arm-none-eabi-gcc").is_file() or not (sdk / "components").is_dir():
        parser.error("Missing SDK/toolchain; run bash tools/setup.sh first")
    revision = subprocess.check_output(["git", "-C", str(sdk), "rev-parse", "HEAD"], text=True)
    if revision.strip() != SDK_REV:
        parser.error(f"SDK must be pinned at {SDK_REV}, got {revision.strip()}")
    build = ROOT / "build"
    if args.clean and build.exists():
        shutil.rmtree(build)
    build.mkdir(exist_ok=True)
    example = sdk / "examples/peripheral/usbd_cdc_acm/pca10056/blank"
    shutil.copyfile(example / "config/sdk_config.h", build / "sdk_defaults.h")
    makefile = (example / "armgcc/Makefile").read_text()
    makefile = makefile.replace("SDK_ROOT := ../../../../../..", f"SDK_ROOT := {sdk}")
    makefile = makefile.replace("PROJ_DIR := ../../..", f"PROJ_DIR := {ROOT / 'firmware'}")
    makefile = makefile.replace("BOARD_PCA10056", "BOARD_PCA10059")
    # Preserve bootloader-provided reset/UICR configuration.
    makefile = makefile.replace(" -DCONFIG_GPIO_AS_PINRESET", "")
    makefile = makefile.replace("  ../config", f"  {ROOT / 'firmware'} \\\n  {build}")
    (build / "Makefile").write_text(makefile)
    linker = (example / "armgcc/usbd_cdc_acm_gcc_nrf52.ld").read_text()
    linker = linker.replace("ORIGIN = 0x0, LENGTH = 0x100000", "ORIGIN = 0x1000, LENGTH = 0xDF000")
    linker = linker.replace(
        "ORIGIN = 0x20000000, LENGTH = 0x40000", "ORIGIN = 0x20000008, LENGTH = 0x3FFF8"
    )
    (build / "usbd_cdc_acm_gcc_nrf52.ld").write_text(linker)
    subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-j",
            str(os.cpu_count() or 2),
            f"GNU_INSTALL_ROOT={gcc}/",
            "GNU_VERSION=10.3.1",
            "GNU_PREFIX=arm-none-eabi",
        ],
        cwd=build,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL"}
        },
        check=True,
    )
    source = build / "_build/nrf52840_xxaa.hex"
    print(f"Verified {verify_hex(source.read_text())} bytes in application-only flash region")
    shutil.copyfile(source, build / "scanner.hex")
    subprocess.run(
        [
            str(ROOT / ".deps/nrfutil"),
            "pkg",
            "generate",
            "--hw-version",
            "52",
            "--sd-req",
            "0x00",
            "--application-version",
            "1",
            "--application",
            str(build / "scanner.hex"),
            str(build / "scanner.zip"),
        ],
        check=True,
    )
    print(f"Application-only DFU package: {build / 'scanner.zip'}")


if __name__ == "__main__":
    main()
