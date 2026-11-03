# Matched storage layout

Both vaults use five full-width `uint256` data words per agent plus one word for
the two flags: **six words / 192 bytes** per record. Fe's nominal wrappers use
full-width values; both compilers naturally place the adjacent booleans into
bytes 0 and 1 of the last word. This is the original representation, not an
additional manually packed variant.

The layout is checked on deployed Fe O2, Solidity legacy and Solidity via-IR
artifacts by `test_semantics.py`, and Solidity's compiler-generated
`storage-layout.json` is retained under `out/bench/<variant>/`. The build gate
checks both field offsets and root slots. Numeric persistent slots and transient
slots belong to separate EVM address spaces.

| Field | Persistent location in both vaults |
|---|---|
| Immutable owner | Runtime code, no persistent storage slot |
| Agent map | Root 0 |
| Epoch map | Root 1 |
| Epoch-scoped payees | Root 2, direct prehashed tuple slot below |
| Global used nonces | Root 3 |
| Paused flag | Slot 4, byte offset 0 |
| Mutex value | Reserved slot 5, remains zero; Solidity reserves a word to match Fe's unused `Mutex<bool>.value` |
| Mutex lock | **Transient slot 6**, shared by spend, approved spend and withdrawal |

`word(x)` is a 32-byte big-endian ABI word (addresses left-padded with zeros).
For agent `a`, epoch `e`, payee `p`, nonce `n`, and concatenation `||`:

```text
agent_base = keccak256(word(a) || word(0))
epoch_slot = keccak256(word(a) || word(1))
payee_slot = keccak256(word(a) || word(e) || word(p) || word(2))
nonce_slot = keccak256(word(n) || word(3))
```

| Record offset from `agent_base` | Contents |
|---:|---|
| 0 | Per-payment cap |
| 1 | Window cap |
| 2 | Period in seconds |
| 3 | Current anchored window start |
| 4 | Gross successful spending in that window |
| 5 | `active + (allowlist_on << 8)` |

Fe's tuple-keyed `StorageMap` hashes all three tuple words and its root **once**.
An ordinary nested Solidity mapping would require three successive hashes.
The mirror therefore declares the payee root, computes the identical four-word
preimage, and uses direct `sload` / `sstore` at that hash. It does not hash the
prehashed key again with `payees[key]`. Epoch and nonce maps use the ordinary
single-key derivation on both sides. Ordinary spending computes the payee lookup
even when allowlisting is disabled, matching Fe's eager policy input.

The deployed check configures caps 10/25 and period 3600 with allowlisting on,
allows a recipient, spends 3, burns nonce 7 and pauses. It reads the actual six
record words (`10,25,3600,start,3,257`), epoch, exact tuple slot, nonce and pause
slots, reserved zero words, and the transient lock's opcode accesses. It then
reconfigures, confirms the old tuple entry remains physically present but the
new epoch entry is zero, and revokes: the flags become 256, retaining allowlist
state while clearing active. A local-only final-epoch injection verifies the
last successful configuration and exact `EpochOverflow()` rollback on both
configuration and revocation.

Matched representation and key derivation do not force identical generated
instructions. The report retains compiler differences in copying, dispatch,
signature handling and redundant reads/writes. Whole records are committed before
external payment in both sources; neither writes the old record back after a
recipient callback. A transient lock has no storage-clearing gas refund. The
`reset-agent` receipt row separately exposes the refund for clearing a nonzero
spent word.
