#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export FE="${FE:-fe}" CAST="${CAST:-cast}" ANVIL="${ANVIL:-anvil}"
"$FE" --version | head -1 | grep -F 'fe 26.5.1 (149c758)'
"$ANVIL" --version | head -1 | grep -F '1.8.7'
"$CAST" --version | head -1 | grep -F '1.8.7'
"$FE" fmt --check
"$FE" test
python3 scripts/compile_fail.py
"$FE" build -O 2 --emit bytecode,runtime-bytecode,abi
python3 bench/build.py --no-fe-build
python3 e2e/test_vault.py
python3 e2e/demo.py --no-build
python3 bench/test_semantics.py
