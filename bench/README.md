# Matched Solidity comparison

`LeashVault.sol` mirrors the concrete Fe vault using bare `uint256` amounts and
times, the same full-width fields, ABI, errors, events and transient lock. Both
builds retain checked arithmetic, live EIP-712 domains, canonical and compact
signatures, persistent epochs, global nonces, gross accounting, exact deadline
semantics, anchored windows, owner administration during callbacks and atomic
transfer failure. There is no imported Solidity vault or signature library.

This is an ergonomics/default-code comparison between the typed Fe source and a
bare-uint Solidity implementation. It is evidence about these implementations and
compiler settings, with no general language performance claim.

## Reproduce

Install Fe **26.5.1 (149c758)**, Anvil/Cast **1.8.7** (commit
`985eeea031d79922cd654d4e94e6c3701e1c7d73`), Node.js (recorded run: **22.23.3**),
npm and Python 3.10+. `FE`, `ANVIL` and `CAST` may be executable paths.

```sh
npm ci --prefix bench --ignore-scripts --no-audit --no-fund
./scripts/check.sh
python3 bench/gas.py
```

`check.sh` keeps the original format, unit, negative-compilation, integration and
demo checks, adds both Solidity builds, and requires semantic equality. CI also
retains the eleven mutation controls. Receipt observations run as a separate
nonblocking CI step and appear in the job summary; there is no gas threshold.
The npm lockfile pins solc **0.8.30+commit.73712a01** and its dependencies with
integrity hashes. After installing tools and npm dependencies, all checks and
measurements are local and need no network or account credentials.

To run only the comparison:

```sh
python3 bench/build.py
python3 bench/test_semantics.py
python3 bench/gas.py
```

The build command compiles Fe with **Sonatina O2**, then Solidity with Osaka,
optimizer enabled, **200 runs**, once using legacy codegen and once using via-IR.
There is no per-row selection between Solidity builds. Fe's original integration
suite, the differential suite and the gas runner all read the same
`out/LeashVault.bin`. `--no-fe-build` is reserved for immediately reusing the O2
build in `check.sh`. Solc's buffered Node adapter avoids truncated standard-JSON
pipe output; it uses the pinned compiler without changing compiler inputs.

Outputs go to ignored `out/bench/`: each Solidity standard-JSON input and output,
ABI, bytecode, storage layout and build metadata; three semantic transcripts and
logs plus `semantics.json`; and the gas runner's `results.json` and `RESULTS.md`.
The committed [results](RESULTS.md), [raw samples](results.json) and
[semantic summary](SEMANTICS.json) record the local run. To deliberately refresh
the committed gas artifacts after building and checking semantics:

```sh
python3 bench/gas.py --output-dir bench
cp out/bench/semantics.json bench/SEMANTICS.json
```

No command deploys outside disposable local Anvil processes. CI has been
configured locally; a hosted run and verification on another platform remain
release tasks.

## Semantic gate

The harness selects each compiled artifact through a temporary artifact directory
and calls the existing `e2e/test_vault.py` suite without changing its assertions.
All **110 named checks**, both chain IDs, the **60-action ordinary corpus** (seed
1729) and **292 mixed actions** (seeds 1729 and 104729) run for **each** compiler.
Four additional checks validate actual storage and transient lock locations,
epoch exhaustion at the final representable value, exact overflow errors and
full-record rollback. The local result is **114 checks and 11,112 observations
per variant**, with identical transcripts for both comparisons to Fe.

Anvil starts with `--hardfork osaka --chain-id 31337 --timestamp 1793664000`
(31338 for the existing second-chain cases), then sets a one-second block
timestamp interval. This removes wall-clock variation between compiler runs;
all explicit timestamp jumps and deadline/boundary assertions are retained.
Opcode tracing is enabled only for the added layout probes and receipt matrix.

The differential transcript compares transaction requests, call returns and exact
revert bytes, mined status and revert bytes, event topics/payloads, sampled
balances/storage, signatures, block timestamps, and chain/time/code/balance
injections. Deployment requests retain the constructor owner while replacing the
compiler-specific initcode in the transcript. Gas, hashes derived from compiler
code or gas, and other receipt metadata are excluded from semantic equality.
State, events, signature bytes and errors are compared without normalization.
The separate build gate also compares ABI types, selectors, event indexing and
mutability, and checks Solidity's emitted record layout.

These are deterministic bounded differential tests, not exhaustive equivalence
or a compiler audit. Artificial full-width balances and final-epoch storage are
limited to local Anvil. Anvil cannot supply a u256-sized timestamp; the existing
Fe endpoint guard tests remain in place.

## Fixed receipt matrix

Every compiler runs every row three times, each on a **fresh Anvil process**.
All samples are retained, and repetitions must agree exactly. The runner uses
the same initial account allocations, funding/configuration transactions,
timestamps, signatures and recipient code across compilers. Before/after state,
raw accounting words, log payloads and statuses must match. Except for initcode,
the measured transaction inputs must also match exactly. Callers' fee-adjusted
balances are excluded from state equality because gas is the measured variable.

The recipient is the same existing unlocked EOA with empty code in every row.
Every measured request supplies a gas limit of **5,000,000**, including failures;
no `estimateGas` result substitutes for a mined receipt. Each transaction starts
with cold storage/account access as specified by the EVM; prior transactions do
not keep slots warm. No access lists or authorization lists are supplied.

| Scenario | Preparation and measured action |
|---|---|
| `deploy` | Deploy with the first unlocked account as immutable owner |
| `fund` | Fresh empty vault; owner funds 100,000 wei |
| `set-agent` | Funded vault; first configuration, caps 10/25, period 3600, allowlisting off |
| `first-spend` / `later-spend` | Configured vault; send 3 wei from spent 0 / after a prior 3-wei send |
| `first-allowlisted-spend` / `later-allowlisted-spend` | Same sends with allowlisting enabled and recipient permitted |
| `rollover-spend` | Spend 3, then measure a 3-wei send mined exactly at the next window boundary |
| `reject-per-tx` | Request 11 against per-payment cap 10 |
| `reject-window` | Spend 10 twice; request 6 against remaining 5 |
| `reject-payee` | Enable allowlisting without permitting the recipient; request 3 |
| `reject-paused` | Pause a configured vault; request 3 |
| `reject-unauthorized-set` | Agent attempts first configuration; floor-bound `Unauthorized()` |
| `approved-65` / `approved-64` | Empty budget counter, allowlisting on and empty; signed 30-wei exception, nonce 7, canonical / compact signature |
| `approved-after-spend` | Spend 3 normally, then use a canonical 30-wei exception |
| `withdraw` | Spend 3 normally, then owner withdraws 3 without changing agent accounting |
| `reset-agent` | Spend 3, then repeat identical configuration; new epoch and clearing refund |

Raw samples include full receipts, actual calldata, gross gas and refund counters
from opcode traces, both intrinsic/floor calculations, bytecode hashes and sizes,
compiler/executable hashes, versions and source hashes. Runtime and initcode are
not stripped. The EIP formulas and floor flags are explained in [RESULTS.md](RESULTS.md).

The table retains losses: Fe costs more for funding and withdrawal than the
legacy build, and costs more to deploy than via-IR. The unauthorized setter is a
floor-bound tie. No row, compiler path or repetition is omitted to improve a
performance claim.
