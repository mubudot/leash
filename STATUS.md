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
- README, MIT license, privacy-conscious ignore rules, pinned CI definition and one-command local verification including an asserted CLI payment walkthrough.
- Matched Solidity mirror, pinned solc legacy/via-IR builds, ABI/layout checks, full-suite semantic differential gate and reproducible receipt-gas comparison.

## Verification evidence

Environment: Fe **26.5.1 (149c758)**, Sonatina build **O2**; Anvil/Cast **1.8.7**, Foundry commit `985eeea031d79922cd654d4e94e6c3701e1c7d73`; local Linux ARM64. Anvil hardfork **Osaka**, initial timestamp **1793664000**. The main suite and demo use chain ID **31337**; domain regressions also change the live chain ID to **31338** and start a fresh process with **31338**. CI targets Ubuntu 24.04 AMD64 with release archives checked by SHA-256.

| Check | Local result |
|---|---|
| `fe fmt --check` | Passed |
| `fe test` | 18 passed, 0 failed |
| `python3 scripts/compile_fail.py` | 5 expected diagnostics at exact locations; all 5 positive controls compile |
| `fe build -O 2 --emit bytecode,runtime-bytecode,abi` | Passed |
| `python3 e2e/test_vault.py` | 110 named checks passed on chains 31337/31338, including 60 seeded ordinary actions and 292 mixed actions |
| `python3 e2e/demo.py` | 22 asserted transactions; decoded events, expected mined errors, balances, headroom and receipt gas |
| `python3 scripts/mutations.py` | Passing baseline; all 11 compiled mutants detected by intended tests |
| `python3 bench/build.py` | solc 0.8.30 legacy and via-IR, Osaka, optimizer 200; ABI and six-word record layout match Fe |
| `python3 bench/test_semantics.py` | 114 checks and 11,112 matching observations per variant; both Solidity transcripts equal Fe |
| `python3 bench/gas.py` | All 18 scenarios x 3 variants x 3 repetitions; matching state/events/calldata and exact receipt/refund/floor accounting |
| Original Phase 1 snapshot (`2145ad0`), fresh local clone, `./scripts/check.sh` | Passed on the same machine with externally installed pinned tools |
| Tracked-file privacy scan | Passed; no private keys or excluded identity text |

The O2 artifact contains 4,501 bytes of creation bytecode (excluding constructor arguments) and 4,354 bytes of runtime bytecode. These are size observations, not comparative gas results.

Negative cases cover asset confusion (8-0000), ETH/time confusion (8-0000), invalid const profile (8-0093), lossy narrowing (8-0055), and absent mutable capability (8-0018).

Integration checks cover two agents, every owner-only handler's authorization, exact caps and cap+1, zero payment on both routes, unset/revoked/paused agents, unchanged reconfiguration, stale allowlist and signature epochs, every signed field and domain field, copied calldata with equal agent epochs, nonce replay/cancellation and cross-agent nonce identity, exact deadline and expiry, canonical and compact signatures, malformed lengths/v/r/s and high-s, ordinary and approved failed sends, withdrawal failures, mined failure receipts without logs, relevant state/balance rollback, exact event payloads on both spend routes, exact/empty balance, full-width accounting overflow, caught funded cross-agent reentry and withdraw-to-spend reentry, lock release, inbound funding without budget refunds, boundary/long-idle views, and executable boundary-burst/four-window-drain limitations.

The domain gate signs on 31337, changes the live chain ID to 31338 without redeploying or changing the approval fields, rejects the old-domain signature with exact mined `InvalidSignature`, and verifies balances, logs, nonce and budget rollback. A fresh RPC-derived 31338 signature then succeeds with exact transfers, nonce use and event payload. A second fresh Anvil process and deployment on 31338 repeat the rejection/success checks. This detects both hardcoded and deployment-time cached domains.

The deadline endpoint is mined at exactly the signed timestamp and checks its persistent transfer, headroom, nonce and event payload; a separate unused approval expires. Funding checks decode non-owner and zero-value `Funded` events while paused and retain gross spending. Additional admin cases cover repeated revocation of an unset agent, idempotent pause/unpause, repeated cancellation of nonce zero, active zero caps, both valid period endpoints and insufficient owner-withdrawal balance. All `rejected` helper cases now verify the mined error as well as the preceding call error. Each transfer route also has a failed-then-successful call in one transaction, checking lock rollback, effects and events.

Direct Fe guard tests check the last representable epoch/time values and exact `EpochOverflow`/`TimeOutOfRange` errors beyond them. Pure window tests reject time before the anchor and outside the numeric domain. Arithmetic tests add subtraction underflow, decimal-exponent overflow at 78 and 255, and the largest representable whole-unit scaling. These exercise the real helpers; they do not simulate an impossible Anvil block timestamp or epoch-exhaustion storage transition.

Payee removal is tested in both ordinary-spend/removal orderings with exact mined `PayeeDenied`, unchanged balances/headroom and no logs; the owner-approved exception remains covered. A relay records both inner results for ordinary/ordinary, approved/approved, ordinary/approved, approved/ordinary and owner-withdrawal/withdrawal calls within one transaction. Tests assert both transfers, budget and nonce effects, and event payloads; withdrawals preserve an active agent's already-used budget. Six caught callback cases cover each outer transfer route targeting ordinary or approved spending, with exact `Reentrant`, unused inner nonces and unchanged inner accounting.

Above-cap accounting has an additional cap-1 vector: an approved send of 2 followed by a same-window request of `U256_MAX - 1` rejects `AccountingOverflow` without consuming the new nonce or transferring funds. The adjacent request of `U256_MAX - 2` succeeds, reaching exactly `U256_MAX`; a further 1 is rejected. Separate empty recipients and synthetic Anvil balances reach these numeric boundaries without patching vault storage. Both successes verify exact event payloads, transfers and nonce use; both failures verify exact mined errors, rollback and no logs.

The independent Python oracles use unbounded integers and derive membership from receipt timestamps and the configuration anchor rather than calling the Fe helper. The original corpus remains **seed 1729, 60 ordinary actions across four windows**. The mixed model independently sums a payment journal by agent, epoch and window. Seeds **1729** and **104729** each execute **146 actions**, including 60 seeded choices after mandatory transition traces: respectively **140 transactions + 6 time jumps** and **138 transactions + 8 time jumps**. They cover successful/failed approvals, replay, cancellation, identical reconfiguration, revocation, epoch-scoped payee updates, pause/unpause and withdrawals, with both payment/admin orderings for pause, revoke, payee removal and cancellation. Every action checks balances, views, epochs and all observed nonces; transactions additionally check exact event payloads or mined errors and rollback. Failures print the seed, index and trace. This is deterministic property testing, not fuzzing. Balance/code injection is isolated to a fresh local Anvil process. No private signing keys are stored or passed on the command line.

Mutation controls flip `>=` at the boundary, remove the approved route's active/pause validation, omit successful-approval nonce consumption, ignore transfer failure after still making the external call, ignore payee removal, omit each transfer route's unlock separately and all three together, clip approved spending to the window cap, and replace live `ctx.chainid()` with 31337. All eleven temporary copies must compile; unrelated compiler failures do not count as detection. The baseline refreshes `out/`; mutant builds remain temporary. Local task instructions, review reports, logs and raw agent outputs (including `fix1_out.md` and rotated agent logs) are ignored and checked with `git check-ignore`.

The CLI walkthrough is implemented in `e2e/demo.py` and included in `scripts/check.sh`, reusing that command's O2 artifact. Its standalone command builds O2 first. The committed `e2e/DEMO.md` supersedes the preparatory outline's obsolete errors and commands. All 22 transactions assert success or exact mined rejection; the separate burst fixture asserts payment receipt timestamps at boundary minus one second and the boundary. Receipt gas is printed without comparative claims.

## Solidity comparison and review 3 I1

The missing Solidity comparison implementation identified by review 3 I1 is complete. `scripts/check.sh` and CI now require both pinned Solidity builds and the semantic differential suite, retaining the original Fe tests, integration assertions and mutation controls. The mirror matches the full-width fields, six-word records with adjacent boolean flags, tuple-payee slot derivation, custom errors/events, live domains, signature forms, gross accounting, transient lock and transfer semantics. Actual storage probes also cover final-epoch configuration/revocation and their exact overflow rollback.

All 110 existing integration checks, both chain IDs and the published 60 ordinary / 292 mixed oracle actions run against each compiler, followed by four layout/epoch checks. The resulting 11,112-observation transcripts match exactly. The committed [semantic summary](bench/SEMANTICS.json) records their digest; full transcripts and compiler inputs/outputs are generated in ignored `out/bench/`.

The [receipt table](bench/RESULTS.md), [raw samples](bench/results.json), [method](bench/README.md) and [layout notes](bench/LAYOUT.md) record the comparison. Each of 18 fixed scenarios starts a fresh Osaka Anvil at timestamp 1793664000 on chain 31337 for each compiler and repetition, with deterministic one-second blocks. All three repetitions agree. Gas is observed without performance thresholds; CI measurement is nonblocking. Fe's 74,445-gas first ordinary spend compares with 75,288 legacy / 75,180 via-IR, but funding and withdrawal cost more than legacy, and Fe deployment costs 1,003,057 versus 984,151 via-IR. The complete table retains these losses and the floor-bound unauthorized-setter tie, with computed intrinsic/floor values and the 4,800-gas configuration-reset refund. Runtime sizes are Fe 4,354 / legacy 5,595 / via-IR 4,297 bytes. These are pinned implementation observations, not a general language ranking.

The comparison was verified locally on Linux ARM64 with Node 22.23.3 and locked solc 0.8.30+commit.73712a01. A hosted CI run and another-platform verification are still unestablished; completing these local I1 deliverables does not close the entire release gate.

## Remaining full-design gate

- Complete remaining reentry permutations and administrative edges, plus a block-context harness for out-of-domain timestamps; actual guard helpers and final-epoch storage transitions have endpoint coverage.
- Further independent security review, fresh environments beyond the recorded local platform, and an actual hosted CI run. CI is configured but has not been pushed or run remotely.
- Submission/release paperwork, reviewed final SHA and any publication remain separate work.

No token vault, ERC-1271 owner validation, packed representation, global/lifetime cap, public deployment, or upgrade path is included. These are the declared design cuts. This implementation is experimental and unaudited.
