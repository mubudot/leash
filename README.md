# Leash

Leash is a Fe library and reference ETH vault that caps what a delegated key can send per payment, per recurring fixed window, and to approved payees, with exact-payment exceptions signed by the owner.

The mechanism follows established delegated-spending patterns. Its contribution is a reusable Fe policy library with nominal asset/time types, generic composition, one compile-time/runtime validator, and a concrete vault with local reproduction tests.

## Run locally

Requirements: **Fe 26.5.1 (149c758)**, **Foundry 1.8.7**, Python 3.10+, Bash, and Linux or macOS. Put `fe`, `cast`, and `anvil` on PATH, or set `FE`, `CAST`, and `ANVIL` to their executable paths. Official downloads: [Fe release](https://github.com/argotorg/fe/releases/tag/v26.5.1), [Foundry release](https://github.com/foundry-rs/foundry/releases/tag/v1.8.7).

```sh
./scripts/check.sh
python3 scripts/mutations.py
```

The first command checks formatting, runs Fe unit tests and negative compilation cases with positive controls, builds **Sonatina O2**, then deploys that same artifact on a fresh local Anvil. It starts Anvil with `--hardfork osaka --chain-id 31337 --timestamp 1793664000`, chooses a free local port, and stops the process afterward. No external chain, wallet, account credentials, Python packages, or network access are needed after installing the tools. Signing uses Anvil's disposable unlocked accounts via `eth_signTypedData_v4`.

The second command establishes a passing baseline, then runs four intentionally broken copies in temporary directories. Each must compile and fail the intended runtime test: boundary comparison, approved-route activity/pause check, nonce consumption, and transfer-success check. Source and build artifacts in the repository remain unchanged.

Individual commands:

```sh
fe test
python3 scripts/compile_fail.py
fe build -O 2 --emit bytecode,runtime-bytecode,abi
python3 e2e/test_vault.py
```

Build artifacts are under `out/`. CI pins download versions and SHA-256 checksums and runs both commands. CI has been configured; local execution evidence is in [STATUS.md](STATUS.md).

## Architecture and Fe example

| Source | Responsibility |
|---|---|
| `src/units.fe` | `Asset`, nominal `Amount<A>`, checked arithmetic/scaling, `Value` over ETH and tagged tokens |
| `src/limits.fe` | `const fn valid_limits`, numeric domain, constrained `Profile<T,C,P>` |
| `src/window.fe` | Pure O(1) anchored fixed-window normalisation and headroom |
| `src/policy.fe` | `Policy<V>`, per-payment/window/payee policies, `All<A,B>` |
| `src/approval.fe` | Exact EIP-712 struct and live domain hashing |
| `src/vault.fe` | Concrete non-upgradeable ETH vault, immutable owner, capabilities and transfer lock |
| `src/errors.fe`, `src/events.fe` | Typed ABI errors and distinct ordinary/approved events |
| `src/tests.fe` | Fe boundary, full-width, generic reuse and arithmetic tests |
| `compile_fail/` | Unit/asset confusion, invalid const profile, mutable-capability and narrowing fixtures |
| `e2e/test_vault.py` | Real callers/signatures/transfers/events, adversarial callbacks, seeded Python oracle |

```fe
// The vault instantiates this generic policy stack for native ETH.
pub type EthPolicy = All<PerTxCap<Wei>, All<FixedWindowCap<Wei>, PayeeAllowlist>>
// The same predicate rejects an invalid Profile at compile time and
// validates owner-supplied limits at runtime.
pub struct Profile<const T: u256, const C: u256, const P: u256>
where valid_limits(T, C, P) {}
```

Policies check immutable inputs before the vault commits effects. The library is also tested over `Amount<MockUsd>`; no ERC-20 vault is included. `Wei`, `Timestamp`, and `Duration` are distinct standard-library types. Their ABI wire representation remains `uint256`, so callers still need to supply correct decimal units. Compile-fail checks require both the intended error code and exact source location, then compile a repaired positive control.

## Vault interface

Deploy `LeashVault` with one nonzero owner address. The owner cannot be replaced. Use the generated ABI in `out/LeashVault.abi.json`.

| Call | Effect |
|---|---|
| `fund()` payable | Adds ETH; emits `Funded` |
| `setAgent(agent, txCap, windowCap, period, allowlistOn)` | Owner sets limits; fresh budget/anchor/epoch and empty epoch allowlist |
| `allowPayee(agent, payee, allowed)` | Owner edits current epoch's allowlist |
| `revoke(agent)` | Owner disables agent and advances persistent epoch |
| `pause()` / `unpause()` | Owner gates both spending routes |
| `cancelApproval(nonce)` | Owner permanently burns a global nonce |
| `withdraw(to, amount)` | Owner recovers ETH, including while paused |
| `spend(payee, amount)` | Caller spends within all ordinary policies |
| `spendApproved(payee, amount, nonce, deadline, epoch, signature)` | Caller uses an exact owner-signed exception |
| `remaining(agent)` | Returns `(headroom, windowEnd)`; inactive/unset returns `(0,0)` |
| `owner()`, `paused()`, `epoch(agent)`, `nonceUsed(nonce)` | Read configuration/replay state |

Amounts are wei; periods and deadlines are seconds. Valid periods are 60 seconds through 365 days inclusive; `txCap <= windowCap`, with full-width u256 caps. Zero caps are valid. Zero payments on both spend routes are rejected. Agent identity is `msg.sender`, including contract callers.

## Transition guarantee

For each agent and configuration epoch, windows are anchored at configuration time: `[anchor + kP, anchor + (k+1)P)`. At the exact endpoint, the next window applies. Skipped windows are normalised with division, without a loop. Time comes only from block context. The numeric domain reserves `MAX_PERIOD` below u256 max so every window end is representable.

An ordinary payment requires an active agent, an unpaused vault, amount no greater than the per-payment cap, sufficient window headroom, and current-epoch payee membership when enabled. All successful payments, including approved exceptions, add their gross amount to the current window's counter. An exception may exceed either cap and bypass the allowlist; later ordinary payments see zero headroom when the total exceeds the window cap. Thus `spent <= windowCap + approvedTotal`, while ordinary sends alone cannot cross the cap.

Both spend routes and owner withdrawals share one transient mutex. Effects and events precede the external call, and send failure reverts the entire transaction. Payments forward remaining gas subject to EIP-150; failure returndata is not copied. Rejection, failed send, insufficient balance, and checked arithmetic failure leave counters, nonces, epochs and balances unchanged and emit no logs. Owner authorization is a runtime check on every administrative handler; Fe capability constraints further narrow field access and external-call authority.

`remaining` computes policy headroom without writing; it ignores pause and vault balance. Inbound ETH never refunds spent accounting. All admin operations are O(1); there is no enumeration.

## Exact-payment approvals

EOA-owner EIP-712 typed data, never personal-sign:

```text
OverCapApproval(address agent,address payee,uint256 amount,uint256 nonce,uint256 deadline,uint256 epoch)
EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)
name = "Leash"; version = "1"
```

The domain uses the live chain ID and this vault's address. The signed agent must equal the caller; all other fields match calldata and the epoch must be current. The deadline is inclusive. Nonces are global across agents, never reset, and consumed only by successful approved payments or owner cancellation. Both canonical 65-byte signatures and 64-byte EIP-2098 encodings are accepted, with low-s, nonzero r/s and v in {27,28}. Replay identity is the nonce, regardless of signature encoding. Ordinary payments never consume a nonce, including nonce zero. ERC-1271 is outside Phase 1.

## Limitations and trust

This is an experimental reference implementation with a **partial verification gate**, not an audited release. See [STATUS.md](STATUS.md) for outstanding work.

- Budgets renew until revocation. Four windows of cap 0.25 ETH can drain a 1 ETH vault. There is no lifetime or vault-wide cap.
- Fixed windows allow a `2C` burst across adjacent endpoints. Both behaviours have executable test traces.
- Agents have independent budgets competing for one balance. An allowlisted payee can be compromised.
- Any `setAgent`, even unchanged, starts a fresh epoch and budget. Old allowlist entries and signatures cannot revive on re-add.
- Removing a payee does not cancel an existing exact-payment approval. Pause preserves approvals; unpause enables them again before expiry. Use cancellation or revocation to invalidate them.
- A stolen agent key can front-run a pending pause, revoke, or cancellation while its request is still valid. Transaction ordering determines the outcome.
- The owner is trusted and can bypass agent restrictions via withdrawal or signed exceptions. Use an EOA owner; contract-signature validation is not implemented.
- ETH can arrive forcibly without `fund()` or an inbound event. The vault does not interpret inbound value as an accounting refund.

## Provenance and license

Author: **mubudot**. [MIT license](LICENSE).

Written from scratch during the build window starting 2026-11-03 00:00 UTC. Pre-existing work: design notes v1–v3.1, threat model, rules research, README/demo drafts and throwaway feasibility experiments. Design prose is adapted here; no spike source is copied. This repository starts empty rather than importing a starter. Fe's standard library is a compiler dependency, not vendored code. Delegated-spending prior art includes [Coinbase Spend Permissions](https://github.com/coinbase/spend-permissions) and [Safe Allowance Module](https://github.com/safe-fndn/safe-modules).

AI assistance: OpenAI Codex generated and checked implementation, tests and documentation under human direction. No network deployment or publication is performed by this project.
