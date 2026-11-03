#!/usr/bin/env python3
"""Receipt gas for a fixed scenario matrix; no performance thresholds or row selection."""
import argparse
import json
import platform
import shutil
import subprocess

from build import FE, OUT, ROOT, SOLC, VARIANTS, artifact, sha
from harness import compiled_vault, h

SCENARIOS = (
    'deploy', 'fund', 'set-agent', 'first-spend', 'later-spend',
    'first-allowlisted-spend', 'later-allowlisted-spend', 'rollover-spend',
    'reject-per-tx', 'reject-window', 'reject-payee', 'reject-paused',
    'reject-unauthorized-set', 'approved-65', 'approved-64',
    'approved-after-spend', 'withdraw', 'reset-agent',
)
REPETITIONS = 3


def intrinsic(payload, creation=False):
    raw = bytes.fromhex(payload.removeprefix('0x'))
    zeros = raw.count(0)
    nonzeros = len(raw) - zeros
    tokens = zeros + 4 * nonzeros
    return {
        'input_bytes': len(raw), 'zero_bytes': zeros, 'nonzero_bytes': nonzeros,
        'standard_intrinsic': 21_000 + 4 * tokens
        + (32_000 + 2 * ((len(raw) + 31) // 32) if creation else 0),
        'calldata_floor': 21_000 + 10 * tokens,
    }


def snapshot():
    """Compare observable state and actual accounting words, even above the cap."""
    def slot(index):
        return h.rpc('eth_getStorageAt', h.V, hex(index), 'latest')

    def key(*words):
        return int(h.cast('keccak', '0x' + ''.join(f'{word:064x}' for word in words)), 16)

    agent = int(h.A, 16)
    base = key(agent, 0)
    return {
        'vault': h.V, 'balance': h.balance(h.V), 'payee_balance': h.balance(h.P),
        'owner': h.read('owner()'), 'paused': h.read('paused()'),
        'epoch': h.read('epoch(address)', h.A), 'remaining': h.read('remaining(address)', h.A),
        'nonce_0': h.read('nonceUsed(uint256)', 0), 'nonce_7': h.read('nonceUsed(uint256)', 7),
        'record': [slot(base + offset) for offset in range(6)],
        'payees': [slot(key(agent, epoch, int(h.P, 16), 2)) for epoch in (1, 2)],
    }


def measure(scenario):
    # One process per scenario AND per repetition AND per compiler, with the
    # same account allocations, timestamps, transactions and recipient code.
    with h.local_chain(steps_tracing=True):
        assert h.rpc('eth_getCode', h.P, 'latest') == '0x'
        if scenario == 'deploy':
            before = None
            receipt = h.deploy()
            expected_event = None
        else:
            h.deploy()
            if scenario != 'fund':
                h.send(h.O, 'fund()', value=100_000)
            if scenario not in ('fund', 'set-agent', 'reject-unauthorized-set'):
                allow = scenario in ('first-allowlisted-spend', 'later-allowlisted-spend',
                                     'reject-payee', 'approved-65', 'approved-64')
                h.configure(allow=allow)
                if scenario in ('first-allowlisted-spend', 'later-allowlisted-spend'):
                    h.send(h.O, 'allowPayee(address,address,bool)', h.A, h.P, True)
                if scenario in ('later-spend', 'later-allowlisted-spend', 'rollover-spend',
                                'approved-after-spend', 'withdraw', 'reset-agent'):
                    h.send(h.A, h.SPEND, h.P, 3)
                if scenario == 'reject-window':
                    h.send(h.A, h.SPEND, h.P, 10)
                    h.send(h.A, h.SPEND, h.P, 10)
                if scenario == 'reject-paused':
                    h.send(h.O, 'pause()')
                if scenario == 'rollover-spend':
                    h.warp(h.read('remaining(address)', h.A)[1] - 1)

            sender, signature, args, value = h.A, h.SPEND, [h.P, 3], 0
            error, expected_event = None, 'Spent(address,address,uint256,uint256)'
            if scenario == 'fund':
                sender, signature, args, value = h.O, 'fund()', [], 100_000
                expected_event = 'Funded(address,uint256)'
            elif scenario in ('set-agent', 'reset-agent', 'reject-unauthorized-set'):
                sender = h.A if scenario == 'reject-unauthorized-set' else h.O
                signature, args = h.SET, [h.A, 10, 25, 3600, False]
                expected_event = 'AgentSet(address,uint256,uint256,uint256,uint256,bool)'
                if sender == h.A:
                    error = 'Unauthorized'
            elif scenario.startswith('reject-'):
                amount, error = {
                    'reject-per-tx': (11, 'PerTxExceeded'),
                    'reject-window': (6, 'WindowExceeded'),
                    'reject-payee': (3, 'PayeeDenied'),
                    'reject-paused': (3, 'Paused'),
                }[scenario]
                args = [h.P, amount]
            elif scenario.startswith('approved-'):
                signature, args = h.APPROVED, h.approval(7, amount=30)
                expected_event = 'SpentApproved(address,address,uint256,uint256,uint256)'
                if scenario == 'approved-64':
                    raw = bytes.fromhex(args[-1][2:])
                    r, s, v = raw[:32], int.from_bytes(raw[32:64], 'big'), raw[64]
                    assert v in (27, 28)
                    args[-1] = '0x' + r.hex() + (s | ((v - 27) << 255)).to_bytes(32, 'big').hex()
            elif scenario == 'withdraw':
                sender, signature = h.O, 'withdraw(address,uint256)'
                expected_event = 'Withdrawn(address,uint256)'

            before = snapshot()
            # Explicit 5,000,000 gas; every rejection is mined, never estimated.
            receipt = h.tx(sender, h.V, h.data(signature, *args), value)
            assert int(receipt['status'], 16) == int(error is None), (scenario, receipt)
            if error:
                h.mined_error(receipt, error)
                assert receipt['logs'] == []
                assert before == snapshot(), (scenario, 'rejection changed state')

        after = snapshot()
        if expected_event and int(receipt['status'], 16):
            assert len(receipt['logs']) == 1
            assert receipt['logs'][0]['topics'] == [h.cast('keccak', expected_event)]
        transaction = h.rpc('eth_getTransactionByHash', receipt['transactionHash'])
        creation = scenario == 'deploy'
        costs = intrinsic(transaction['input'], creation)
        gas_used = int(receipt['gasUsed'], 16)
        trace = h.rpc('debug_traceTransaction', receipt['transactionHash'],
                      {'disableMemory': True, 'disableStorage': True, 'disableStack': True})
        first, last = trace['structLogs'][0], trace['structLogs'][-1]
        assert first['depth'] == last['depth'] == 1
        assert last['op'] in ('RETURN', 'STOP', 'REVERT'), last
        gas_limit = int(transaction['gas'], 16)
        assert gas_limit - first['gas'] == costs['standard_intrinsic']
        runtime = h.rpc('eth_getCode', h.V, 'latest')
        # RETURN ends constructor execution before the 200 gas/byte deposit.
        deposit = 200 * ((len(runtime) - 2) // 2) if creation else 0
        gross = gas_limit - last['gas'] + last['gasCost'] + deposit
        refund_counter = last['refund'] if not trace['failed'] else 0
        refund = min(refund_counter, gross // 5)
        net = gross - refund
        assert gas_used == max(net, costs['calldata_floor']), (scenario, gas_used, gross, refund, costs)
        return {
            'scenario': scenario, 'status': int(receipt['status'], 16),
            **costs, 'gas_used': gas_used,
            'charged_above_intrinsic': gas_used - costs['standard_intrinsic'],
            'gross_gas': gross, 'refund_counter': refund_counter, 'capped_refund': refund,
            'floor_topup': max(0, costs['calldata_floor'] - net),
            'floor_bound': net <= costs['calldata_floor'],
            'transaction_hash': receipt['transactionHash'],
            'receipt': receipt, 'block_timestamp': h.now(),
            'input': transaction['input'], 'before': before, 'after': after,
            'logs': [{key: log[key] for key in ('address', 'topics', 'data')} for log in receipt['logs']],
        }


def environment():
    def version(executable):
        return subprocess.check_output([executable, '--version'], text=True).strip()

    versions = {'fe': version(FE), 'solc': version(SOLC), 'anvil': version(h.ANVIL),
                'cast': version(h.CAST), 'node': version('node'), 'npm': version('npm'),
                'python': platform.python_version()}
    assert versions['fe'] == 'fe 26.5.1 (149c758)'
    assert '0.8.30+commit.73712a01' in versions['solc']
    for name in ('anvil', 'cast'):
        assert '1.8.7' in versions[name] and '985eeea031d79922cd654d4e94e6c3701e1c7d73' in versions[name]
    return {
        'versions': versions, 'platform': f'{platform.system()} {platform.machine()}',
        'executable_sha256': {name: sha(artifact_path.read_bytes()) for name, artifact_path in {
            'fe': ROOT / shutil.which(FE), 'anvil': ROOT / shutil.which(h.ANVIL),
            'cast': ROOT / shutil.which(h.CAST),
            'soljson': ROOT / 'bench/node_modules/solc/soljson.js',
        }.items()},
        'hardfork': 'osaka', 'chain_id': 31337, 'genesis_timestamp': 1793664000,
        'block_timestamp_interval': 1, 'gas_limit': 5_000_000,
        'fe_settings': 'Sonatina -O 2', 'solc_settings': 'optimizer 200, evmVersion osaka',
        'source_sha256': {str(path.relative_to(ROOT)): sha(path.read_bytes())
                          for path in sorted((ROOT / 'src').glob('*.fe'))
                          + sorted((ROOT / 'e2e').glob('*.py')) + [ROOT / 'fe.toml']
                          + [ROOT / ('bench/' + name) for name in (
                              'LeashVault.sol', 'package-lock.json', 'build.py', 'solc.cjs',
                              'harness.py', 'test_semantics.py', 'gas.py')]},
    }


def render(report):
    lines = [
        '# Receipt gas comparison', '',
        'Generated by `python3 bench/gas.py` from the fixed matrix in `gas.py`. '
        'All 18 scenarios and all three repetitions are included; repetitions must agree exactly. '
        'The JSON contains every sample, calldata, logs, state, hashes and compiler versions.', '',
        f"Measured on {report['environment']['platform']}, Fe 26.5.1 (149c758), "
        'Anvil/Cast 1.8.7 (985eeea031d79922cd654d4e94e6c3701e1c7d73), solc '
        '0.8.30+commit.73712a01; Node ' + report['environment']['versions']['node'] + '.', '',
        'Fe uses Sonatina O2. Both Solidity builds use Osaka and optimizer runs 200. '
        'Legacy and via-IR are separate columns throughout. Each sample starts a fresh Anvil '
        'at timestamp 1793664000, chain 31337, Osaka, with one-second block increments. '
        'See [method and reproduction](README.md) and [storage layout](LAYOUT.md).', '',
        'This is a bare-uint-versus-typed ergonomics/default-code comparison. '
        'It measures these implementations and pinned builds; it establishes no general language gas ranking.', '',
        '| Scenario | Status | Fe O2 | Solidity legacy | Solidity via-IR |',
        '|---|---:|---:|---:|---:|',
    ]
    for scenario in SCENARIOS:
        rows = [report['samples'][variant][scenario][0] for variant in VARIANTS]
        values = [f"{row['gas_used']:,}" + (' *' if row['floor_bound'] else '') for row in rows]
        lines.append(f"| {scenario} | {rows[0]['status']} | " + ' | '.join(values) + ' |')
    lines += ['', '`*` means the calldata floor binds. Status 0 is an intentionally mined rejection. '
              'Receipt gas includes intrinsic costs, effective refunds and any calldata-floor top-up.', '',
              '| Variant | Creation bytes | Runtime bytes | Creation SHA-256 |',
              '|---|---:|---:|---|']
    for variant in VARIANTS:
        item = report['artifacts'][variant]
        lines.append(f"| {variant} | {item['creation_bytes']:,} | {item['runtime_bytes']:,} | "
                     f"`{item['creation_sha256']}` |")
    lines += ['', 'Creation size excludes the 32-byte owner argument. Runtime size includes '
              'the immutable owner area and compiler metadata where emitted; nothing is stripped.', '',
              '## Intrinsic, floor and refund accounting', '',
              'Each cell below is **standard intrinsic / calldata floor / charged above intrinsic / '
              'capped refund / floor top-up**. These are gas units computed from that sample\'s '
              'actual input bytes and opcode trace. Charged above intrinsic is receipt gas minus '
              'standard intrinsic; it includes any floor charge and is not an execution-only metric.', '',
              '| Scenario | Fe O2 | Solidity legacy | Solidity via-IR |',
              '|---|---:|---:|---:|']
    for scenario in SCENARIOS:
        values = []
        for variant in VARIANTS:
            row = report['samples'][variant][scenario][0]
            values.append(' / '.join(f'{row[key]:,}' for key in (
                'standard_intrinsic', 'calldata_floor', 'charged_above_intrinsic',
                'capped_refund', 'floor_topup')))
        lines.append(f'| {scenario} | ' + ' | '.join(values) + ' |')
    lines += ['', 'For `Z` zero and `N` nonzero input bytes, tokens = `Z + 4N`. '
              'Standard intrinsic is `21000 + 4*tokens`, plus `32000 + 2*ceil(input_bytes/32)` '
              'for creation. The floor is `21000 + 10*tokens`. '
              'The reported receipt is checked against `max(gross - min(refund_counter, gross//5), floor)`. '
              'Gross comes from the top-level trace gas balance, plus code deposit for creation. '
              'Resetting an already-spent agent clears its spent word and earns a refund; the transient '
              'mutex earns none. Sources: [EIP-7623](https://eips.ethereum.org/EIPS/eip-7623), '
              '[EIP-3860](https://eips.ethereum.org/EIPS/eip-3860), '
              '[EIP-3529](https://eips.ethereum.org/EIPS/eip-3529).', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=lambda value: ROOT / value, default=OUT,
                        help='write results.json and RESULTS.md here; defaults to ignored out/bench')
    args = parser.parse_args()
    report = {'schema': 1, 'environment': environment(), 'repetitions': REPETITIONS,
              'artifacts': {}, 'samples': {}}
    for variant in VARIANTS:
        creation = bytes.fromhex(artifact(variant).read_text().strip())
        runtime = bytes.fromhex(artifact(variant, 'runtime.bin').read_text().strip())
        report['artifacts'][variant] = {
            'creation_bytes': len(creation), 'runtime_bytes': len(runtime),
            'creation_sha256': sha(creation), 'runtime_sha256': sha(runtime),
        }
        if variant != 'fe':
            report['artifacts'][variant]['build'] = json.loads((OUT / variant / 'build.json').read_text())
        report['samples'][variant] = {}
        with compiled_vault(variant):
            for scenario in SCENARIOS:
                rows = [measure(scenario) for _ in range(REPETITIONS)]
                assert all(row == rows[0] for row in rows), (variant, scenario, 'unstable repetitions')
                report['samples'][variant][scenario] = rows
        print(f'PASS receipt matrix {variant}: {len(SCENARIOS)} scenarios x {REPETITIONS} identical samples', flush=True)
    for scenario in SCENARIOS:
        baseline = report['samples']['fe'][scenario][0]
        for variant in VARIANTS[1:]:
            row = report['samples'][variant][scenario][0]
            for key in ('status', 'before', 'after', 'logs', 'block_timestamp'):
                assert row[key] == baseline[key], (scenario, variant, key)
            if scenario != 'deploy':
                assert row['input'] == baseline['input'], (scenario, variant, 'different calldata')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.output_dir / 'RESULTS.md').write_text(render(report))
    print(f'Wrote {args.output_dir / "RESULTS.md"} and results.json; no gas thresholds applied.', flush=True)


if __name__ == '__main__':
    main()
