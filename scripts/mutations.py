#!/usr/bin/env python3
"""Deliberately break safety conditions in temporary copies; require runtime detection."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FE = os.environ.get('FE', 'fe')


def run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=180)


# Establish that the same unmodified suite passes before measuring controls.
for command in ([FE, 'test'], [FE, 'build', '-O', '2', '--emit', 'bytecode,runtime-bytecode,abi'], ['python3', 'e2e/test_vault.py']):
    result = run(command, ROOT)
    assert result.returncode == 0, result.stdout + result.stderr

for name in ('boundary', 'approved_activity', 'nonce_consumption', 'send_success', 'payee_removal',
             'spend_unlock', 'approved_unlock', 'withdraw_unlock', 'all_unlocks', 'approved_clipping',
             'hardcoded_chain_id'):
    with tempfile.TemporaryDirectory(prefix='leash-mutation-') as temp:
        project = Path(temp)
        shutil.copy(ROOT / 'fe.toml', project / 'fe.toml')
        shutil.copytree(ROOT / 'src', project / 'src')
        shutil.copytree(ROOT / 'e2e', project / 'e2e')
        target = project / 'src' / ('window.fe' if name == 'boundary' else 'vault.fe')
        original = target.read_text()
        if name == 'boundary':
            changed = original.replace('if elapsed >= p {', 'if elapsed > p {')
            expected = 'half_open_window_and_skipped_periods'
        elif name == 'approved_activity':
            start = original.index('        SpendApproved { payee')
            changed = original[:start] + original[start:].replace('require_active(record, paused)', '', 1)
            expected = 'expected revert: Paused'
        elif name == 'nonce_consumption':
            changed = original.replace('agents[agent] = record\n            used_nonces[nonce] = true', 'agents[agent] = record')
            expected = 'expected revert: NonceUsed'
        elif name == 'send_success':
            changed = original.replace('.success() {', '.success() && false {')
            expected = 'expected revert: TransferFailed'
        elif name == 'payee_removal':
            changed = original.replace('payees[(agent, epoch, payee)] = allowed', 'payees[(agent, epoch, payee)] = true')
            expected = 'expected revert: PayeeDenied'
        elif name == 'all_unlocks':
            assert original.count('guard.unlock()') == 3
            changed = original.replace('guard.unlock()', '')
            expected = 'sequential spend/spend unlock'
        elif name == 'approved_clipping':
            start = original.index('        SpendApproved { payee')
            assignment = 'record.spent = checked_spent(window.spent, amount)'
            clipping = assignment + '\n            if record.spent > record.window_cap {\n                record.spent = record.window_cap\n            }'
            changed = original[:start] + original[start:].replace(assignment, clipping, 1)
            expected = 'expected revert: AccountingOverflow'
        elif name == 'hardcoded_chain_id':
            assert original.count('ctx.chainid()') == 1
            changed = original.replace('ctx.chainid()', '31337')
            expected = 'live chain ID domain must reject the 31337 signature'
        else:
            route = {'spend_unlock': 0, 'approved_unlock': 1, 'withdraw_unlock': 2}[name]
            parts = original.split('guard.unlock()')
            assert len(parts) == 4
            changed = 'guard.unlock()'.join(parts[:route+1]) + 'guard.unlock()'.join(parts[route+1:])
            expected = {'spend_unlock': 'sequential spend/spend unlock',
                        'approved_unlock': 'sequential approved/approved unlock',
                        'withdraw_unlock': 'sequential withdraw unlock'}[name]
        assert changed != original, name
        target.write_text(changed)
        if name == 'boundary':
            result = run([FE, 'test'], project)
        else:
            build = run([FE, 'build', '-O', '2', '--emit', 'bytecode'], project)
            assert build.returncode == 0, build.stdout + build.stderr
            result = run(['python3', 'e2e/test_vault.py'], project)
        output = result.stdout + result.stderr
        assert result.returncode != 0 and expected in output, (name, output)
        print(f'PASS mutation {name}: compiled mutant rejected by intended test', flush=True)
