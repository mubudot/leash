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
| `python3 e2e/test_vault.py` | 73 named checks passed, plus 60 seeded ordinary actions |
| `python3 scripts/mutations.py` | Passing baseline; all 4 compiled mutants detected by intended tests |
| Tracked-file privacy scan | Passed; no private keys or excluded identity text |

Negative cases cover asset confusion (8-0000), ETH/time confusion (8-0000), invalid const profile (8-0093), lossy narrowing (8-0055), and absent mutable capability (8-0018).

Integration checks cover two agents, every owner-only handler's authorization, exact caps and cap+1, zero payment on both routes, unset/revoked/paused agents, unchanged reconfiguration, stale allowlist and signature epochs, every signed field and domain field, copied calldata with equal agent epochs, nonce replay/cancellation and cross-agent nonce identity, exact deadline and expiry, canonical and compact signatures, malformed lengths/v/r/s and high-s, ordinary and approved failed sends, withdrawal failures, mined failure receipts without logs, relevant state/balance rollback, exact event payloads on both spend routes, exact/empty balance, full-width accounting overflow, caught funded cross-agent reentry and withdraw-to-spend reentry, lock release, inbound funding without budget refunds, boundary/long-idle views, and executable boundary-burst/four-window-drain limitations.

The independent Python oracle uses unbounded integers and derives membership from receipt timestamps and the configuration anchor rather than calling the Fe helper. Its corpus is **seed 1729, 60 ordinary actions across four windows**. This is deterministic property testing, not fuzzing. Anvil-only balance/code injection creates adversarial recipients and a synthetic maximum-balance accounting case; it is isolated to a fresh local process. No private signing keys are stored or passed on the command line.

Mutation controls flip `>=` at the boundary, remove the approved route's active/pause validation, omit successful-approval nonce consumption, and ignore transfer failure after still making the external call. Temporary copies must compile; unrelated compiler failures do not count as detection.

## Remaining full-design gate

- Expand the oracle corpus to mixed approvals and administrative transitions, additional published seeds, and both orderings of spend versus pause/revoke/remove-payee/cancel.
- Add further same-transaction relay/reentry permutations, epoch-overflow and out-of-domain context harnesses, and exhaustive admin event payload assertions.
- Build and semantically verify the matched Solidity mirror, then measure receipt gas, intrinsic/floor accounting, layout and deploy/runtime sizes under the design's methodology. No gas advantage is claimed.
- Independent security review, fresh environments beyond the recorded local platform, and an actual hosted CI run. CI is configured but has not been pushed or run remotely.
- Submission/release paperwork, reviewed final SHA and any publication remain separate work.

No token vault, ERC-1271 owner validation, packed representation, global/lifetime cap, public deployment, or upgrade path is included. These are the declared design cuts. This implementation is experimental and unaudited.
