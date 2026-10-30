#!/usr/bin/env bash
set -euo pipefail
ROOT="$(dirname "$(dirname "$(realpath "$0")")")"
DEPS="$ROOT/.deps"
mkdir -p "$DEPS"
if [[ ! -d "$DEPS/nrf5-sdk" ]]; then
    git clone https://github.com/greenlsi/nrf5-sdk.git "$DEPS/nrf5-sdk"
    git -C "$DEPS/nrf5-sdk" checkout bd4841e2e9b459b1ac45781807db5cedad2ce633
fi
if [[ ! -d "$DEPS/arm-gcc" ]]; then
    curl -fL --retry 2 -o "$DEPS/gcc.tar.gz" \
        https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/download/v10.3.1-2.3/xpack-arm-none-eabi-gcc-10.3.1-2.3-linux-x64.tar.gz
    tar -xzf "$DEPS/gcc.tar.gz" -C "$DEPS"
    mv "$DEPS/xpack-arm-none-eabi-gcc-10.3.1-2.3" "$DEPS/arm-gcc"
fi
if [[ ! -x "$DEPS/nrfutil" ]]; then
    curl -fL --retry 2 -o "$DEPS/nrfutil" \
        https://github.com/NordicSemiconductor/pc-nrfutil/releases/download/v6.1.7/nrfutil-linux
    chmod +x "$DEPS/nrfutil"
fi
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements-dev.txt"
