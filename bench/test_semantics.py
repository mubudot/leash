#!/usr/bin/env python3
"""Run every existing integration assertion and compare semantic RPC transcripts."""
from contextlib import redirect_stdout
from itertools import zip_longest
import json
import traceback

from build import OUT, VARIANTS, sha
from harness import compiled_vault, h


def mapping_slot(*words):
    return int(h.cast('keccak', '0x' + ''.join(f'{word:064x}' for word in words)), 16)


def storage_layout():
    """Read actual Fe/Solidity storage, including tuple keys and packed flags."""
    with h.local_chain(steps_tracing=True):
        h.deploy()
        h.send(h.O, 'fund()', value=100)
        h.configure(allow=True)
        start = h.now()
        h.send(h.O, 'allowPayee(address,address,bool)', h.A, h.P, True)
        receipt = h.send(h.A, h.SPEND, h.P, 3)
        h.send(h.O, 'cancelApproval(uint256)', 7)
        h.send(h.O, 'pause()')
        base = mapping_slot(int(h.A, 16), 0)
        epoch = mapping_slot(int(h.A, 16), 1)
        payee = mapping_slot(int(h.A, 16), 1, int(h.P, 16), 2)
        nonce = mapping_slot(7, 3)

        def slot(index):
            return int(h.rpc('eth_getStorageAt', h.V, hex(index), 'latest'), 16)

        assert [slot(base + i) for i in range(6)] == [10, 25, 3600, start, 3, 257]
        assert [slot(epoch), slot(payee), slot(nonce), slot(4), slot(5)] == [1, 1, 1, 1, 0]
        assert [slot(i) for i in range(4)] == [0, 0, 0, 0]
        trace = h.rpc('debug_traceTransaction', receipt['transactionHash'],
                      {'disableMemory': True, 'disableStorage': True})
        top_depth = trace['structLogs'][0]['depth']
        locks = [(step['op'], int(step['stack'][-1], 16)) for step in trace['structLogs']
                 if step['depth'] == top_depth and step['op'] in ('TLOAD', 'TSTORE')]
        assert locks == [('TLOAD', 6), ('TSTORE', 6), ('TSTORE', 6)], locks
        h.send(h.O, 'unpause()')
        h.configure(allow=True)
        assert slot(payee) == 1
        assert slot(mapping_slot(int(h.A, 16), 2, int(h.P, 16), 2)) == 0
        h.send(h.O, 'revoke(address)', h.A)
        assert slot(base + 5) == 256 and slot(epoch) == 3
        h.mark('matched six-word records, packed flags, tuple payees, roots, transient slot 6')

        # Actual final-epoch handlers, using the verified slot only on local Anvil.
        h.rpc('anvil_setStorageAt', h.V, hex(epoch), '0x' + f'{h.MAX - 1:064x}')
        h.configure(t=1, c=2, allow=True)
        h.send(h.O, 'allowPayee(address,address,bool)', h.A, h.P, True)
        h.send(h.A, h.SPEND, h.P, 1)
        before = [slot(base + i) for i in range(6)]
        assert slot(epoch) == h.MAX
        h.rejected(h.O, h.SET, [h.A, 2, 3, 3600, False], 'EpochOverflow')
        h.rejected(h.O, 'revoke(address)', [h.A], 'EpochOverflow')
        assert [slot(base + i) for i in range(6)] == before
        h.mark('final representable epoch, exact overflow errors and full record rollback')


def run_variant(variant):
    transcript = []
    original_rpc = h.rpc

    def observe(method, *params):
        # Gas, transaction/block hashes and compiler-specific creation code are
        # intentionally excluded. No state, event or revert value is normalised.
        request = list(params)
        if method == 'eth_sendTransaction':
            request = [dict(params[0])]
            if 'to' not in request[0]:
                request[0]['data'] = 'constructor:' + request[0]['data'][-64:]
            transcript.append([method, request])
        try:
            result = original_rpc(method, *params)
        except h.RpcError as ex:
            if method == 'eth_call':
                transcript.append([method, request, {'revert': ex.error.get('data')}])
            raise
        if method in ('eth_call', 'eth_getBalance', 'eth_getStorageAt', 'eth_signTypedData_v4'):
            transcript.append([method, request, result])
        elif method == 'eth_getTransactionReceipt' and result is not None:
            transcript.append(['receipt', {
                'status': result['status'], 'contractAddress': result['contractAddress'],
                'blockNumber': result['blockNumber'],
                'logs': [{key: log[key] for key in ('address', 'topics', 'data')} for log in result['logs']],
            }])
        elif method == 'debug_traceTransaction':
            if 'output' in result:
                transcript.append(['mined revert', result['output']])
        elif method == 'eth_getBlockByNumber':
            transcript.append([method, request, result['timestamp']])
        elif method.startswith('anvil_set') or method in ('evm_setNextBlockTimestamp', 'evm_mine'):
            transcript.append([method, request])
        return result

    OUT.mkdir(parents=True, exist_ok=True)
    log_path = OUT / f'semantics-{variant}.log'
    try:
        h.rpc, h.N = observe, 0
        with compiled_vault(variant):
            with log_path.open('w') as log, redirect_stdout(log):
                try:
                    h.main()  # All 110 checks, both chains and all published corpora.
                    storage_layout()
                except Exception:
                    traceback.print_exc(file=log)
                    raise
        (OUT / f'transcript-{variant}.json').write_text(json.dumps(transcript, indent=2) + '\n')
        print(f'PASS semantics {variant}: {h.N} checks; {len(transcript)} observations', flush=True)
        return transcript
    except Exception:
        if log_path.exists():
            print(log_path.read_text()[-8000:])
        raise
    finally:
        h.rpc = original_rpc


def main():
    baseline = run_variant('fe')
    digest = sha(json.dumps(baseline, sort_keys=True).encode())
    for variant in VARIANTS[1:]:
        candidate = run_variant(variant)
        for index, (expected, actual) in enumerate(zip_longest(baseline, candidate)):
            assert expected == actual, (variant, index, expected, actual)
        print(f'PASS differential Fe vs {variant}: identical semantic transcript', flush=True)
    (OUT / 'semantics.json').write_text(json.dumps({
        'variants': VARIANTS, 'observations_per_variant': len(baseline),
        'transcript_sha256': digest, 'checks_per_variant': h.N,
        'ordinary_seed': 1729, 'ordinary_actions': 60,
        'mixed_seeds': [1729, 104729], 'mixed_actions': 292,
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
