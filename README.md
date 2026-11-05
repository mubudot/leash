# Leash

Leash is a Fe spending-policy library and reference ETH vault for delegated keys. An owner gives each agent a per-payment cap, a recurring fixed-window budget and, optionally, an approved-payee list. The agent can pay within that policy; an exact owner-signed approval can authorize a specific exception.

The library combines nominal asset and time types, generic policies and a shared compile-time/runtime limit validator. The vault demonstrates their use with real ETH transfers, EIP-712 approvals and reproducible local tests.

**Demo video (1:51):** https://youtu.be/uhCKq3Aynmw

**Experimental and unaudited.** The implementation has local verification evidence, but the full release gate remains open. See [status and outstanding work](STATUS.md).

## Install and verify

Run commands from the repository root. Install these tools first:

| Tool | Verified version | Purpose |
|---|---|---|
| [Fe](https://github.com/argotorg/fe/releases/tag/v26.5.1) | 26.5.1 (`149c758`) | Format, test and compile Fe |
| [Foundry](https://github.com/foundry-rs/foundry/releases/tag/v1.8.7) | Anvil/Cast 1.8.7 | Disposable local EVM and ABI tooling |
| Python | 3.12.15 in the recorded run; scripts require 3.10+ | Test and benchmark harnesses; standard library only |
| Node.js / npm | 22.23.3 / 10.9.9 | Run the locked Solidity compiler |
| Bash | Available on the verified Linux environment | Run the verification script |

Put `fe`, `cast` and `anvil` on `PATH`, or export `FE`, `CAST` and `ANVIL` with their executable paths. Standalone `fe` commands below assume it is on `PATH`. The recorded local platform is **Linux aarch64**. The configured CI targets Ubuntu 24.04 AMD64; a hosted CI run and verification on another platform remain outstanding.

Install the locked benchmark dependency, then run the checks:

```sh
npm ci --prefix bench --ignore-scripts --no-audit --no-fund
./scripts/check.sh
python3 scripts/mutations.py
```

The npm command installs **solc 0.8.30** and its locked dependencies. After installing tools and npm dependencies, verification runs locally without network access, account credentials or additional Python packages.

`check.sh` verifies tool versions and formatting, runs Fe unit tests and negative-compilation fixtures with repaired positive controls, builds Fe with **Sonatina O2**, builds both Solidity variants, runs the vault integration suite and payment demo, then checks semantic equality across all three compiled variants. CI also runs the mutation controls; receipt-gas measurements are a separate nonblocking step.

The local checks cover:

- **18 Fe tests** and **5 negative-compilation fixtures**, each with an expected diagnostic and a compiling positive control.
- **110 integration checks**, including real callers, signatures, transfers, exact mined errors, event payloads, rollback, callbacks and both chain IDs 31337/31338.
- **60 ordinary actions** with seed 1729 and **292 mixed actions** with seeds 1729 and 104729, checked against independent Python accounting models. Failures print the seed, action index and trace.
- **114 checks and 11,112 observations per compiler variant** in the differential suite, including four additional storage-layout and epoch-boundary checks.
- **11 mutation controls**: temporary broken copies must compile and fail the intended test. They target window boundaries, approved-route activity/pause checks, nonce consumption, transfer success, payee removal, transfer-lock release, approved accounting and the live chain-ID domain. The baseline refreshes `out/`; mutant source and artifacts remain in temporary directories.

Each harness owns and stops its disposable Anvil processes. They use Osaka and initial timestamp `1793664000`; the main suite and demo use chain ID 31337, with domain regressions also exercising 31338. Tests sign through Anvil's disposable unlocked accounts using `eth_signTypedData_v4`. No private signing keys are stored in the repository or supplied on the command line.

### Build or run individual checks

```sh
fe test
python3 scripts/compile_fail.py
fe build -O 2 --emit bytecode,runtime-bytecode,abi
python3 e2e/test_vault.py
```

The integration command uses the existing build. Outputs are under ignored `out/`, including `LeashVault.bin`, `LeashVault.runtime.bin` and `LeashVault.abi.json`. The vault uses a transient mutex; the verified execution target is **Osaka**. Check target-chain EVM support before considering a deployment.

For the standalone payment walkthrough:

```sh
python3 e2e/demo.py
```

It builds O2, then asserts **22 transactions** while printing balances, headroom, decoded events, expected mined errors and receipt gas. The flow covers ordinary payments, an exact approval, replay rejection, revocation, withdrawal and a separate fixed-window burst trace. See the [demo guide](e2e/DEMO.md).

## Benchmark results

After `check.sh`, reproduce the receipt matrix with:

```sh
python3 bench/gas.py
```

Generated measurements go to `out/bench/`. The committed [full results](bench/RESULTS.md) and [raw samples](bench/results.json) contain **18 scenarios × 3 compiler variants × 3 repetitions**. Every repetition must agree exactly, and the harness checks matching state, events, calldata and transaction status across variants.

The recorded run uses Fe 26.5.1 Sonatina O2 and solc 0.8.30 with Osaka, optimizer runs 200, separately using legacy and via-IR code generation. Each sample starts a fresh deterministic local Anvil. Selected **receipt gas** from that run:

| Scenario | Fe O2 | Solidity legacy | Solidity via-IR |
|---|---:|---:|---:|
| Deploy | 1,003,057 | 1,265,023 | 984,151 |
| Fund | 22,688 | 22,546 | 22,739 |
| Configure agent | 160,191 | 160,866 | 160,848 |
| First ordinary spend | 74,445 | 75,288 | 75,180 |
| Later ordinary spend | 57,345 | 58,188 | 58,080 |
| Window-rollover spend | 57,456 | 58,428 | 58,207 |
| Approved spend, 65-byte signature | 100,934 | 102,564 | 101,840 |
| Approved spend, 64-byte signature | 100,796 | 102,456 | 101,720 |
| Reject while paused | 37,135 | 37,079 | 37,131 |
| Reject unauthorized configuration | 23,480 | 23,480 | 23,480 |
| Owner withdrawal | 33,253 | 33,194 | 33,212 |
| Reset agent | 44,391 | 45,066 | 45,048 |

Fe uses less gas for the recorded ordinary and approved payments. It costs more to fund than Solidity legacy, more to deploy than via-IR, and more for withdrawal and paused rejection than either Solidity build. Unauthorized configuration is a calldata-floor-bound tie. These observations describe the pinned implementations and settings; they establish no general language gas ranking.

| Bytecode size | Fe O2 | Solidity legacy | Solidity via-IR |
|---|---:|---:|---:|
| Creation bytes, excluding owner argument | 4,501 | 5,780 | 4,463 |
| Runtime bytes | 4,354 | 5,595 | 4,297 |

Receipt gas includes intrinsic costs, effective refunds and any calldata-floor top-up. The reset-agent row includes a 4,800-gas refund. Intentionally rejected transactions are mined failures. The [complete table](bench/RESULTS.md) retains every scenario and explains intrinsic/floor/refund accounting; the [benchmark method](bench/README.md), [storage layout](bench/LAYOUT.md) and [semantic summary](bench/SEMANTICS.json) describe the comparison gate. Bounded differential tests do not prove exhaustive equivalence.

## Library and vault

| Source | Responsibility |
|---|---|
| `src/units.fe` | Nominal `Amount<A>`, tagged assets, checked arithmetic and decimal scaling |
| `src/limits.fe` | Shared `const fn valid_limits` and constrained `Profile<T,C,P>` |
| `src/window.fe` | O(1) anchored fixed-window normalization and headroom |
| `src/policy.fe` | Generic per-payment, window and payee policies; `All<A,B>` composition |
| `src/approval.fe` | Exact EIP-712 approval and live domain hashing |
| `src/vault.fe` | ETH vault, immutable owner, authorization and transient transfer lock |
| `src/errors.fe`, `src/events.fe` | Typed ABI errors and distinct ordinary/approved payment events |
| `src/tests.fe`, `compile_fail/` | Boundary/arithmetic tests and rejected type/capability examples |
| `e2e/` | Integration checks, independent accounting models, seeded corpora and demo |
| `bench/` | Matched Solidity implementation, semantic comparison and receipt measurements |

The vault instantiates this generic policy stack:

```fe
pub type EthPolicy = All<PerTxCap<Wei>, All<FixedWindowCap<Wei>, PayeeAllowlist>>
```

The same predicate validates runtime configuration and constrains compile-time profiles:

```fe
pub struct Profile<const T: u256, const C: u256, const P: u256>
where valid_limits(T, C, P) {}
```

The library is also tested with `Amount<MockUsd>`; the concrete vault supports **native ETH only**. `Wei`, `Timestamp` and `Duration` are distinct Fe types, although their ABI representation remains `uint256`. Callers must supply amounts in wei and time values in seconds.

### Vault interface

Deploy `LeashVault` with one nonzero owner address and use the generated ABI at `out/LeashVault.abi.json`. The owner cannot be replaced, and there is no upgrade mechanism.

| Call | Effect |
|---|---|
| `fund()` payable | Deposit ETH; emit `Funded` |
| `setAgent(agent, txCap, windowCap, period, allowlistOn)` | Owner configures a fresh budget, anchor and epoch with an initially empty epoch allowlist |
| `allowPayee(agent, payee, allowed)` | Owner edits the current epoch's allowlist |
| `revoke(agent)` | Owner disables the agent and advances its persistent epoch |
| `pause()` / `unpause()` | Owner gates both spending routes |
| `cancelApproval(nonce)` | Owner permanently consumes a global approval nonce |
| `withdraw(to, amount)` | Owner recovers ETH, including while paused |
| `spend(payee, amount)` | Caller pays within ordinary policy |
| `spendApproved(payee, amount, nonce, deadline, epoch, signature)` | Caller uses an exact owner-signed exception |
| `remaining(agent)` | Return `(headroom, windowEnd)`; inactive/unset returns `(0,0)` |
| `owner()`, `paused()`, `epoch(agent)`, `nonceUsed(nonce)` | Read owner, pause, epoch and replay state |

Valid periods range from **60 seconds to 365 days**, inclusive. Caps are full-width `u256`, with `txCap <= windowCap`; zero caps are valid. Both spending routes reject zero payments. The agent is always `msg.sender`, which may be a contract. Administrative operations are O(1); the vault offers no enumeration.

## Security model

### Ordinary payments and recurring budgets

For a configuration anchor `a` and period `P`, windows are `[a + kP, a + (k+1)P)`. The exact endpoint belongs to the next window. Normalization skips elapsed windows using division rather than a loop; time comes from block context. The timestamp domain reserves the maximum period below `u256` maximum so window ends remain representable.

An ordinary payment requires an active agent, an unpaused vault, an amount within the per-payment cap, sufficient window headroom and current-epoch payee membership when allowlisting is enabled. Every successful payment adds its **gross amount** to that window's spent counter, including approved exceptions. An exception may exceed either cap and bypass the allowlist; after it exhausts the budget, ordinary headroom is zero until renewal or reconfiguration.

`remaining` is a read-only policy view: it ignores pause state and vault balance. Deposits do not refund spent accounting. Agents have independent budgets but share one ETH balance.

### Exact-payment approvals

The EOA owner signs EIP-712 typed data with this exact schema:

```text
OverCapApproval(address agent,address payee,uint256 amount,uint256 nonce,uint256 deadline,uint256 epoch)
EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)
name = "Leash"; version = "1"
```

The domain binds the approval to the **live chain ID and vault address**. The agent must match the caller, every signed payment field must match calldata, and the epoch must be current. The deadline is inclusive. Use typed-data signing; personal-sign signatures are not accepted.

Approved payments still require an active agent, an unpaused vault, sufficient ETH balance and checked accounting without overflow.

Nonces are global across all agents, never reset, and are consumed only by a successful approved payment or owner cancellation. Ordinary payments do not consume nonces. The verifier accepts canonical 65-byte ECDSA and 64-byte EIP-2098 signatures, requiring low-s, nonzero r/s and canonical v values 27/28 after decoding. The nonce identifies replay across both encodings. ERC-1271 contract-owner signatures are not implemented.

### Authorization, transfers and rollback

Every administrative handler checks the caller against the immutable owner at runtime. Fe capability declarations additionally constrain field access and external-call authority.

Ordinary spending, approved spending and owner withdrawals share one transient mutex. State effects and events precede the external value call; send failure reverts the whole transaction. Calls forward remaining gas subject to EIP-150, and failure returndata is not copied. A rejected payment, failed send, insufficient balance or checked arithmetic failure rolls back counters, nonces, epochs, value transfers and logs. A mined failure still costs transaction gas.

The mutex protects the three transfer routes. Other owner administration can execute during callbacks when the owner is a contract; that owner remains fully trusted. The supported approval flow requires an EOA owner.

## Limits and trust assumptions

- **Recurring authority has no lifetime limit.** A 0.25 ETH budget renewed over four windows can drain a 1 ETH vault. There is no vault-wide cap, agent expiry or automatic stop after a cumulative amount.
- **Fixed windows allow boundary bursts.** An agent can spend up to `2C` across adjacent endpoints. This and the recurring drain have executable test traces.
- **The owner controls the funds.** Owner withdrawals and signed exceptions can bypass agent caps. The owner is immutable; there is no built-in recovery or rotation if that key is lost or compromised.
- **Reconfiguration grants a fresh budget.** Any `setAgent`, even with identical arguments, advances the epoch and resets accounting and allowlist membership. Revocation also advances the epoch. Old signatures and payee entries cannot revive when an agent is re-added.
- **Payee removal and pause preserve approvals.** An exact approval can still authorize a removed payee. Unpausing re-enables unexpired approvals. Use nonce cancellation, revocation or reconfiguration to invalidate the corresponding authority.
- **Transaction order matters.** A stolen agent key can spend before a pending pause, revocation or cancellation is mined. An approved payee can also be compromised.
- **Inbound ETH does not restore budget.** ETH can arrive forcibly without calling `fund()` or emitting an inbound event.
- **Scope is deliberately limited.** There is no ERC-20 vault, ERC-1271 validation, global/lifetime cap or upgrade path. The repository does not deploy to a public chain.

The tests provide bounded local evidence. Further independent security review, remaining callback/admin permutations, a block-context harness for out-of-domain timestamps, another-platform verification and an actual hosted CI run remain part of the [open release gate](STATUS.md).

## Provenance and license

Author: **mubudot**. Released under the [MIT license](LICENSE).

Written from scratch during the build window starting 2026-11-03 00:00 UTC. Pre-existing work consisted of design notes v1–v3.1, a threat model, rules research, README/demo drafts and disposable feasibility experiments. Design prose was adapted; no experimental source was copied. The repository started empty, and Fe's standard library is a compiler dependency rather than vendored code.

Delegated-spending prior art includes [Coinbase Spend Permissions](https://github.com/coinbase/spend-permissions) and [Safe Allowance Module](https://github.com/safe-fndn/safe-modules). Leash applies those established patterns through a reusable Fe policy library and a concrete reference vault.

AI assistance: OpenAI Codex generated and checked implementation, tests and documentation under human direction.
