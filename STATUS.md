# Phase 1 status

Implemented from scratch on 2026-11-03 after 00:00 UTC. Local commits only, authored by `mubudot`. Phase 1 core vault and spend-limit library are implemented and locally verified. The full design's release gate remains open.

## Implemented

- Nominal ETH/time boundaries and generic tagged-token amounts, checked amount arithmetic and decimal scaling.
- One `const fn valid_limits` for constrained compile-time profiles and runtime configuration.
- O(1) anchored fixed windows, saturating headroom, inactive-record guard, full-width numerical domain.
- Generic per-payment, window and payee policies with immutable check composition, tested for ETH and a tagged token.
- Concrete ETH vault: immutable nonzero owner, configuration, persistent checked epochs, epoch-scoped allowlist, revoke, pause, funding, recovery withdrawal and both spend routes.
- EIP-712 exact-payment approvals bound to caller/payee/amount/nonce/deadline/epoch and live chain/address domain; canonical ECDSA plus EIP-2098; global nonce use/cancellation.
- Checked approved accounting, distinct typed errors/events, a shared transient mutex, checked transfer success and atomic rollback.
- README, MIT license, privacy-conscious ignore rules, pinned CI definition and one-command local verification.

## Verification evidence

Environment: Fe **26.5.1 (149c758)**, Sonatina build **O2**; Anvil/Cast **1.8.7**, Foundry commit `985eeea031d79922cd654d4e94e6c3701e1c7d73`; local Linux ARM64. Anvil hardfork **Osaka**, chain ID **31337**, initial timestamp **1793664000**. CI targets Ubuntu 24.04 AMD64 with release archives checked by SHA-256.

| Check | Local result |
|---|---|
| `fe fmt --check` | Passed |
| `fe test` | 9 passed, 0 failed |
| `python3 scripts/compile_fail.py` | 5 expected diagnostics at exact locations; all 5 positive controls compile |
| `fe build -O 2 --emit bytecode,runtime-bytecode,abi` | Passed |
| `python3 e2e/test_vault.py` | 93 named checks passed, including 60 seeded ordinary actions and 292 mixed actions |
| `python3 scripts/mutations.py` | Passing baseline; all 10 compiled mutants detected by intended tests |
| Original Phase 1 snapshot (`2145ad0`), fresh local clone, `./scripts/check.sh` | Passed on the same machine with externally installed pinned tools |
| Tracked-file privacy scan | Passed; no private keys or excluded identity text |

The O2 artifact contains 4,501 bytes of creation bytecode (excluding constructor arguments) and 4,354 bytes of runtime bytecode. These are size observations, not comparative gas results.

Negative cases cover asset confusion (8-0000), ETH/time confusion (8-0000), invalid const profile (8-0093), lossy narrowing (8-0055), and absent mutable capability (8-0018).

Integration checks cover two agents, every owner-only handler's authorization, exact caps and cap+1, zero payment on both routes, unset/revoked/paused agents, unchanged reconfiguration, stale allowlist and signature epochs, every signed field and domain field, copied calldata with equal agent epochs, nonce replay/cancellation and cross-agent nonce identity, exact deadline and expiry, canonical and compact signatures, malformed lengths/v/r/s and high-s, ordinary and approved failed sends, withdrawal failures, mined failure receipts without logs, relevant state/balance rollback, exact event payloads on both spend routes, exact/empty balance, full-width accounting overflow, caught funded cross-agent reentry and withdraw-to-spend reentry, lock release, inbound funding without budget refunds, boundary/long-idle views, and executable boundary-burst/four-window-drain limitations.

Payee removal is tested in both ordinary-spend/removal orderings with exact mined `PayeeDenied`, unchanged balances/headroom and no logs; the owner-approved exception remains covered. A relay records both inner results for ordinary/ordinary, approved/approved, ordinary/approved, approved/ordinary and owner-withdrawal/withdrawal calls within one transaction. Tests assert both transfers, budget and nonce effects, and event payloads; withdrawals preserve an active agent's already-used budget. Six caught callback cases cover each outer transfer route targeting ordinary or approved spending, with exact `Reentrant`, unused inner nonces and unchanged inner accounting.

Above-cap accounting has an additional cap-1 vector: an approved send of 2 followed by a same-window request of `U256_MAX - 1` rejects `AccountingOverflow` without consuming the new nonce or transferring funds. The adjacent request of `U256_MAX - 2` succeeds, reaching exactly `U256_MAX`; a further 1 is rejected. Separate empty recipients and synthetic Anvil balances reach these numeric boundaries without patching vault storage. Both successes verify exact event payloads, transfers and nonce use; both failures verify exact mined errors, rollback and no logs.

The independent Python oracles use unbounded integers and derive membership from receipt timestamps and the configuration anchor rather than calling the Fe helper. The original corpus remains **seed 1729, 60 ordinary actions across four windows**. The mixed model independently sums a payment journal by agent, epoch and window. Seeds **1729** and **104729** each execute **146 actions**, including 60 seeded choices after mandatory transition traces: respectively **140 transactions + 6 time jumps** and **138 transactions + 8 time jumps**. They cover successful/failed approvals, replay, cancellation, identical reconfiguration, revocation, epoch-scoped payee updates, pause/unpause and withdrawals, with both payment/admin orderings for pause, revoke, payee removal and cancellation. Every action checks balances, views, epochs and all observed nonces; transactions additionally check exact event payloads or mined errors and rollback. Failures print the seed, index and trace. This is deterministic property testing, not fuzzing. Balance/code injection is isolated to a fresh local Anvil process. No private signing keys are stored or passed on the command line.

Mutation controls flip `>=` at the boundary, remove the approved route's active/pause validation, omit successful-approval nonce consumption, ignore transfer failure after still making the external call, ignore payee removal, omit each transfer route's unlock separately and all three together, and clip approved spending to the window cap. All ten temporary copies must compile; unrelated compiler failures do not count as detection. Local task instructions, review reports, logs and raw agent outputs are ignored.

## Remaining full-design gate

- Complete remaining reentry permutations, epoch-overflow and out-of-domain context harnesses, funding-event payload assertions and administrative edge cases.
- Build and semantically verify the matched Solidity mirror, then measure receipt gas, intrinsic/floor accounting, layout and deploy/runtime sizes under the design's methodology. No gas advantage is claimed.
- Further independent security review, fresh environments beyond the recorded local platform, and an actual hosted CI run. CI is configured but has not been pushed or run remotely.
- Submission/release paperwork, reviewed final SHA and any publication remain separate work.

No token vault, ERC-1271 owner validation, packed representation, global/lifetime cap, public deployment, or upgrade path is included. These are the declared design cuts. This implementation is experimental and unaudited.
