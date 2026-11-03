#!/usr/bin/env python3
"""Asserted local payment walkthrough with ABI-decoded events and receipt gas."""
import argparse
import json
import os
import subprocess

import test_vault as h

ETH = 10**18
DAY = 86400


def eth(amount):
    whole, fractional = divmod(amount, ETH)
    return f'{whole}.{fractional:018d}'.rstrip('0').rstrip('.')


class Walkthrough:
    def __init__(self):
        abi = json.loads((h.ROOT / 'out/LeashVault.abi.json').read_text())
        self.events = {}
        for entry in abi:
            if entry['type'] == 'event':
                signature = entry['name'] + '(' + ','.join(i['type'] for i in entry['inputs']) + ')'
                self.events[h.cast('keccak', signature)] = entry
        self.gas = []
        self.actors = {address.lower(): label for address, label in
                       ((h.O, 'owner'), (h.A, 'agent'), (h.B, 'agent2'),
                        (h.P, 'shop'), (h.G, 'unknown'))}

    def snapshot(self, nonce):
        return (h.balance(h.V), h.balance(h.P), h.balance(h.G),
                h.read('remaining(address)', h.A), h.read('epoch(address)', h.A),
                h.read('remaining(address)', h.B), h.read('epoch(address)', h.B),
                h.read('nonceUsed(uint256)', nonce))

    def record(self, label, receipt, error=None):
        assert int(receipt['status'], 16) == int(error is None), (label, receipt)
        self.gas.append((label, int(receipt['gasUsed'], 16)))
        print(f'PASS {label}' + (f' -> {error}() (expected rejection)' if error else ''), flush=True)
        if error:
            h.mined_error(receipt, error)
            assert receipt['logs'] == []
        for log in receipt['logs']:
            assert log['address'].lower() == h.V.lower()
            entry = self.events[log['topics'][0]]
            assert len(log['topics']) == 1 and not any(i['indexed'] for i in entry['inputs'])
            raw = log['data'][2:]
            assert len(raw) == 64 * len(entry['inputs'])
            decoded = []
            for index, field in enumerate(entry['inputs']):
                value = int(raw[64*index:64*(index+1)], 16)
                if field['type'] == 'address':
                    address = f'0x{value:040x}'
                    value = self.actors.get(address, address)
                elif field['type'] == 'bool':
                    assert value in (0, 1)
                    value = bool(value)
                elif field['name'] in ('amount', 'tx_cap', 'window_cap'):
                    value = f'{eth(value)} ETH'
                else:
                    assert field['type'] == 'uint256'
                decoded.append(f'{field["name"]}={value}')
            print(f'  {entry["name"]}(' + ', '.join(decoded) + ')')
        print(f'  vault balance={eth(h.balance(h.V))} ETH')

    def call(self, label, sender, signature, *args, value=0, error=None):
        nonce = args[2] if signature == h.APPROVED else 0
        before = self.snapshot(nonce)
        receipt = h.tx(sender, h.V, h.data(signature, *args), value)
        self.record(label, receipt, error)
        if error:
            assert self.snapshot(nonce) == before, (label, 'rejected call changed state')
        return receipt

    def budget(self, agent, expected):
        amount, end = h.read('remaining(address)', agent)
        assert amount == expected, (amount, expected)
        print(f'  headroom={eth(amount)} ETH; window end={end}')
        return end


def run():
    demo = Walkthrough()
    print(f'Local Osaka chain {int(h.rpc("eth_chainId"), 16)}; signing with disposable unlocked accounts.')
    print(f'owner={h.O}; agent={h.A}; agent2={h.B}; shop={h.P}')
    receipt = h.deploy()
    print(f'vault={h.V}')
    demo.record('deploy', receipt)
    demo.call('fund 1 ETH', h.O, 'fund()', value=ETH)
    demo.call('configure agent: 0.1 ETH/payment, 0.25 ETH/day, allowlist on',
              h.O, h.SET, h.A, ETH // 10, ETH // 4, DAY, True)
    demo.call('allow shop', h.O, 'allowPayee(address,address,bool)', h.A, h.P, True)
    for index in (1, 2):
        demo.call(f'ordinary payment {index}: 0.1 ETH', h.A, h.SPEND, h.P, ETH // 10)
    end = demo.budget(h.A, ETH // 20)
    demo.call('third 0.1 ETH exceeds window cap', h.A, h.SPEND, h.P, ETH // 10, error='WindowExceeded')
    demo.call('unknown payee: 0.01 ETH', h.A, h.SPEND, h.G, ETH // 100, error='PayeeDenied')
    demo.call('ordinary 0.5 ETH exceeds payment cap', h.A, h.SPEND, h.P, ETH // 2, error='PerTxExceeded')
    # Equal epochs let the copied calldata reach signature verification.
    demo.call('configure agent2 with the same epoch', h.O, h.SET, h.B, ETH // 10, ETH // 4, DAY, False)
    signed = h.approval(1, amount=ETH // 2)
    print(f'  owner signs exact 0.5 ETH approval; domain={json.dumps(h.approval_domain(), sort_keys=True)}')
    demo.call('copied approval submitted by agent2', h.B, h.APPROVED, *signed, error='InvalidSignature')
    assert h.read('nonceUsed(uint256)', 1) == (0,)
    before = h.balance(h.P)
    demo.call('approved 0.5 ETH exception', h.A, h.APPROVED, *signed)
    assert h.balance(h.P) == before + ETH // 2
    assert h.read('nonceUsed(uint256)', 1) == (1,)
    demo.budget(h.A, 0)
    demo.call('replay the same approval', h.A, h.APPROVED, *signed, error='NonceUsed')
    h.warp(end)
    print(f'Advance to the next window: timestamp={h.now()}')
    demo.budget(h.A, ETH // 4)
    demo.call('ordinary 0.1 ETH after renewal', h.A, h.SPEND, h.P, ETH // 10)
    demo.budget(h.A, 3 * ETH // 20)
    demo.call('revoke agent', h.O, 'revoke(address)', h.A)
    assert h.read('remaining(address)', h.A) == (0, 0)
    demo.call('revoked agent tries to pay', h.A, h.SPEND, h.P, 1, error='UnknownAgent')
    demo.call('owner withdraws remaining 0.2 ETH', h.O, 'withdraw(address,uint256)', h.P, ETH // 5)
    assert h.balance(h.V) == 0

    receipt = h.deploy()
    print(f'Fresh vault for the fixed-window burst limitation: {h.V}')
    demo.record('burst: deploy', receipt)
    demo.call('burst: fund 0.6 ETH', h.O, 'fund()', value=3 * ETH // 5)
    demo.call('burst: configure agent2 for 0.25 ETH/day', h.O, h.SET, h.B, ETH // 4, ETH // 4, DAY, False)
    boundary = demo.budget(h.B, ETH // 4)
    for timestamp, suffix in ((boundary - 1, 'boundary minus one second'), (boundary, 'exact boundary')):
        h.rpc('evm_setNextBlockTimestamp', timestamp)
        receipt = demo.call(f'burst: 0.25 ETH at {suffix}', h.B, h.SPEND, h.P, ETH // 4)
        block = h.rpc('eth_getBlockByNumber', receipt['blockNumber'], False)
        assert int(block['timestamp'], 16) == timestamp
        assert h.read('remaining(address)', h.B) == (0, boundary if timestamp < boundary else boundary + DAY)
    assert h.balance(h.V) == ETH // 10
    print('Observed limitation: 0.5 ETH paid across adjacent windows in one second.')
    print('\nReceipt gasUsed (local O2 artifact; includes expected mined rejections):')
    for label, gas in demo.gas:
        print(f'  {gas:>7}  {label}')
    print(f'All {len(demo.gas)} demo transactions passed their assertions.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-build', action='store_true', help='use the existing O2 artifacts in out/')
    args = parser.parse_args()
    if not args.no_build:
        result = subprocess.run([os.environ.get('FE', 'fe'), 'build', '-O', '2', '--emit',
                                 'bytecode,runtime-bytecode,abi'], cwd=h.ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert result.returncode == 0, result.stdout
        print('Built Fe Sonatina O2 artifacts in out/.')
    with h.local_chain():
        run()


if __name__ == '__main__':
    main()
