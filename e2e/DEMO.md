# Local payment walkthrough

Run `python3 e2e/demo.py` from the repository root with the pinned tools described
in the README. The command builds Sonatina O2 and starts its own disposable local
Anvil on Osaka, chain 31337, with initial timestamp 1793664000. It signs typed
data through Anvil's unlocked accounts and stops Anvil on exit. It requires no
external chain or account credentials. `--no-build` reuses existing artifacts.

The executable walkthrough asserts these steps and prints balances, headroom,
ABI-decoded events and actual receipt `gasUsed`:

1. Deploy; fund 1 ETH; configure an agent for 0.1 ETH per payment and 0.25 ETH per
   day; allow a shop.
2. Pay the shop 0.1 ETH twice; observe 0.05 ETH of headroom. Mine the next rejected
   payment with `WindowExceeded()`.
3. Mine rejected requests for an unknown payee (`PayeeDenied()`) and an ordinary
   0.5 ETH payment (`PerTxExceeded()`).
4. Sign an exact 0.5 ETH approval bound to the RPC chain ID, vault, agent, payee,
   amount, nonce, deadline and epoch. A second agent with the same epoch fails
   with `InvalidSignature()` and leaves the nonce unused. The intended agent's
   payment succeeds; a replay fails with `NonceUsed()`.
5. Move to the next anchored window; pay 0.1 ETH. Revoke the agent, verify
   `UnknownAgent()`, then withdraw the remaining 0.2 ETH.
6. Deploy a separate vault funded with 0.6 ETH. With a 0.25 ETH daily cap, mine
   0.25 ETH payments exactly at boundary minus one second and at the boundary.
   Verify the two receipt timestamps and accounting, demonstrating the documented
   fixed-window burst of twice the cap.

Every expected rejection checks the exact mined error, no events and unchanged
balances, budgets, epochs and relevant nonce. Gas output is a walkthrough of the
Fe artifact; the matched Solidity comparison and intrinsic/floor analysis remain
on the release checklist in `STATUS.md`.

This guide supersedes the preparatory demo outline's obsolete error names,
signing command and negative-compilation command. The compile-time safety clip is
`python3 scripts/compile_fail.py`, with an expected diagnostic and a compiling
positive control for each case. The full local gate is `./scripts/check.sh`,
followed by `python3 scripts/mutations.py`.
