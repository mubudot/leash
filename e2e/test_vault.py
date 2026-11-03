#!/usr/bin/env python3
"""Local Anvil integration checks. Python stdlib + cast; no signing keys in source."""
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import time
import urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ROOT = Path(__file__).resolve().parents[1]
CAST = os.environ.get('CAST', 'cast')
ANVIL = os.environ.get('ANVIL', 'anvil')
SET = 'setAgent(address,uint256,uint256,uint256,bool)'
SPEND = 'spend(address,uint256)'
APPROVED = 'spendApproved(address,uint256,uint256,uint256,uint256,bytes)'
MAX = 2**256 - 1
N = 0


def cast(*args):
    return subprocess.check_output([CAST, *map(str, args)], text=True).strip()


def rpc(method, *params):
    body = json.dumps(dict(jsonrpc='2.0', id=1, method=method, params=params)).encode()
    with OPENER.open(urllib.request.Request(URL, body, {'Content-Type': 'application/json'}), timeout=10) as r:
        data = json.load(r)
    if 'error' in data:
        raise RpcError(data['error'])
    return data['result']


class RpcError(Exception):
    def __init__(self, error):
        self.error = error
        super().__init__(str(error))


def data(sig, *args):
    return cast('calldata', sig, *[str(a).lower() if isinstance(a, bool) else a for a in args])


def tx(sender, to=None, payload='0x', value=0):
    request = dict(from_=sender, data=payload, value=hex(value), gas=hex(5_000_000))
    request['from'] = request.pop('from_')
    if to:
        request['to'] = to
    h = rpc('eth_sendTransaction', request)
    for _ in range(200):
        receipt = rpc('eth_getTransactionReceipt', h)
        if receipt is not None: break
        time.sleep(0.01)
    assert receipt is not None
    return receipt


def send(sender, sig, *args, value=0, to=None):
    receipt = tx(sender, to or V, data(sig, *args), value)
    assert int(receipt['status'], 16) == 1, (sig, receipt)
    return receipt


def read(sig, *args):
    raw = rpc('eth_call', {'to': V, 'data': data(sig, *args)}, 'latest')[2:]
    return tuple(int(raw[i:i+64], 16) for i in range(0, len(raw), 64))


def balance(addr):
    return int(rpc('eth_getBalance', addr, 'latest'), 16)


def now():
    return int(rpc('eth_getBlockByNumber', 'latest', False)['timestamp'], 16)


def warp(timestamp):
    rpc('evm_setNextBlockTimestamp', timestamp)
    rpc('evm_mine')


def mark(label):
    global N
    N += 1
    print(f'PASS {N:03d} {label}', flush=True)


def mined_error(receipt, error):
    trace = rpc('debug_traceTransaction', receipt['transactionHash'], {'tracer': 'callTracer'})
    assert trace['output'] == cast('sig', error + '()'), (error, trace)


def event(log, signature, words):
    assert log['address'].lower() == V.lower()
    assert log['topics'] == [cast('keccak', signature)]
    actual = [int(log['data'][i:i+64], 16) for i in range(2, len(log['data']), 64)]
    assert actual == words, (signature, actual, words)


def install_relay(address, payloads):
    # Anvil-only EVM relay: CALL each payload, catch failure, and store each
    # success flag and first return word in slots 2*i and 2*i+1. No compiler
    # or external package is needed for this adversarial test fixture.
    def push(value):
        raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), 'big')
        return bytes([0x5f + len(raw)]).hex() + raw.hex()

    code = ''
    for index, payload in enumerate(payloads):
        raw = payload.removeprefix('0x')
        assert len(raw) // 2 < 4096
        code += push(0) + push(4096) + '52'
        for offset in range(0, len(raw), 64):
            code += '7f' + raw[offset:offset+64].ljust(64, '0') + push(offset // 2) + '52'
        # CALL(out_size=32, out=4096, in_size, in=0, value=0, vault, GAS).
        code += push(32) + push(4096) + push(len(raw) // 2) + push(0) + push(0)
        code += '73' + V[2:] + '5af1' + push(2 * index) + '55'
        code += push(4096) + '51' + push(2 * index + 1) + '55'
    rpc('anvil_setCode', address, '0x' + code + '00')


def relay_results(address, count):
    return [tuple(int(rpc('eth_getStorageAt', address, hex(2*i+j), 'latest'), 16)
                  for j in range(2)) for i in range(count)]


def rejected(sender, sig, args, error, nonce=0):
    payload = data(sig, *args)
    # Check the exact typed error before mining the same failing request.
    try:
        rpc('eth_call', {'from': sender, 'to': V, 'data': payload}, 'latest')
        raise AssertionError('expected revert: ' + error)
    except RpcError as ex:
        expected = cast('sig', error + '()')
        assert ex.error.get('data', '').startswith(expected), (error, ex.error)
    # Windows can move as blocks mine; these cases use long periods.
    before = (read('remaining(address)', A), read('epoch(address)', A), read('nonceUsed(uint256)', nonce), balance(V), balance(P))
    receipt = tx(sender, V, payload)
    assert int(receipt['status'], 16) == 0
    assert receipt['logs'] == []
    after = (read('remaining(address)', A), read('epoch(address)', A), read('nonceUsed(uint256)', nonce), balance(V), balance(P))
    assert before == after, (error, before, after)
    mark(error)
    return receipt


def configure(agent=None, t=10, c=25, period=3600, allow=False):
    return send(O, SET, agent or A, t, c, period, allow)


def approval(nonce, amount=30, agent=None, payee=None, epoch=None, deadline=None, domain=None):
    agent, payee = agent or A, payee or P
    epoch = read('epoch(address)', agent)[0] if epoch is None else epoch
    deadline = now() + 1800 if deadline is None else deadline
    typed = {
        'types': {
            'EIP712Domain': [{'name': n, 'type': t} for n, t in [('name','string'),('version','string'),('chainId','uint256'),('verifyingContract','address')]],
            'OverCapApproval': [{'name': n, 'type': t} for n, t in [('agent','address'),('payee','address'),('amount','uint256'),('nonce','uint256'),('deadline','uint256'),('epoch','uint256')]],
        },
        'primaryType': 'OverCapApproval',
        'domain': domain or dict(name='Leash', version='1', chainId=31337, verifyingContract=V),
        'message': dict(agent=agent, payee=payee, amount=str(amount), nonce=str(nonce), deadline=str(deadline), epoch=str(epoch)),
    }
    sig = rpc('eth_signTypedData_v4', O, json.dumps(typed))
    return [payee, amount, nonce, deadline, epoch, sig]


def ordinary_and_admin():
    assert read('owner()')[0] == int(O, 16)
    assert read('remaining(address)', A) == (0, 0)
    rejected(A, SPEND, [P, 1], 'UnknownAgent')
    configure(allow=True)
    rejected(A, SPEND, [P, 1], 'PayeeDenied')
    send(O, 'allowPayee(address,address,bool)', A, P, True)
    rejected(A, SPEND, [P, 11], 'PerTxExceeded')
    rejected(A, SPEND, [P, 0], 'ZeroAmount')
    b = balance(P)
    r = send(A, SPEND, P, 10)
    assert balance(P) == b + 10 and read('remaining(address)', A)[0] == 15
    # Event signature and every ABI payload word.
    assert r['logs'][0]['topics'] == [cast('keccak', 'Spent(address,address,uint256,uint256)')]
    words = [int(r['logs'][0]['data'][i:i+64], 16) for i in range(2, len(r['logs'][0]['data']), 64)]
    assert words == [int(A,16), int(P,16), 10, read('remaining(address)', A)[1]-3600]
    send(A, SPEND, P, 10)
    rejected(A, SPEND, [P, 6], 'WindowExceeded')
    send(A, SPEND, P, 5)
    assert read('remaining(address)', A)[0] == 0
    assert read('nonceUsed(uint256)', 0) == (0,)
    mark('exact caps, transfer, Spent payload, ordinary nonce 0 untouched')
    configure(B)
    send(B, SPEND, P, 10)
    mark('two agents have independent budgets')
    admin = [(SET, [A,10,25,3600,False]), ('allowPayee(address,address,bool)',[A,P,True]), ('revoke(address)',[A]), ('pause()',[]), ('unpause()',[]), ('cancelApproval(uint256)',[90]), ('withdraw(address,uint256)',[P,1])]
    for sig,args in admin:
        rejected(A, sig, args, 'Unauthorized')
    for t,c,p in [(26,25,3600),(1,25,59),(1,25,31_536_001)]:
        rejected(O, SET, [A,t,c,p,False], 'InvalidLimits')
    configure(allow=True)
    rejected(A, SPEND, [P,1], 'PayeeDenied')
    send(O, 'allowPayee(address,address,bool)', A,P,True)
    old = read('epoch(address)', A)[0]
    send(O, 'revoke(address)', A)
    assert read('remaining(address)', A) == (0,0)
    rejected(A, SPEND, [P,1], 'UnknownAgent')
    configure(allow=True)
    assert read('epoch(address)', A)[0] == old+2
    rejected(A, SPEND, [P,1], 'PayeeDenied')
    mark('unchanged set resets epoch; stale allowlist cannot resurrect')


def payee_removal():
    for spend_first in (True, False):
        configure(allow=True)
        send(O, 'allowPayee(address,address,bool)', A, P, True)
        before = balance(V), balance(P)
        end = read('remaining(address)', A)[1]
        if spend_first:
            send(A, SPEND, P, 1)
            assert (balance(V), balance(P)) == (before[0] - 1, before[1] + 1)
            assert read('remaining(address)', A) == (24, end)
        send(O, 'allowPayee(address,address,bool)', A, P, False)
        receipt = rejected(A, SPEND, [P, 1], 'PayeeDenied')
        mined_error(receipt, 'PayeeDenied')
        spent = int(spend_first)
        assert (balance(V), balance(P)) == (before[0] - spent, before[1] + spent)
        assert read('remaining(address)', A) == (25 - spent, end)
        mark(f'payee removal: spend_first={spend_first}, exact mined denial and rollback')


def approvals():
    configure(allow=True)
    signed = approval(1)
    receipt = send(A, APPROVED, *signed)
    assert receipt['logs'][0]['topics'] == [cast('keccak', 'SpentApproved(address,address,uint256,uint256,uint256)')]
    words=[int(receipt['logs'][0]['data'][i:i+64],16) for i in range(2,len(receipt['logs'][0]['data']),64)]
    assert words == [int(A,16),int(P,16),signed[1],signed[2],read('remaining(address)',A)[1]-3600]
    assert read('remaining(address)', A)[0] == 0
    rejected(A, APPROVED, signed, 'NonceUsed', 1)
    configure(B)
    duplicate = approval(1, agent=B)
    rejected(B, APPROVED, duplicate, 'NonceUsed', 1)
    mark('approved bypasses caps and payees, exact accounting and global nonce')
    configure()
    # Equal epochs ensure copied calldata reaches signature verification.
    while read('epoch(address)',B)[0] < read('epoch(address)',A)[0]: configure(B)
    signed = approval(2)
    # Every signed field is bound, including caller and current epoch.
    rejected(B, APPROVED, signed, 'InvalidSignature', 2)
    for index,replacement in [(0,B),(1,31),(2,3),(3,signed[3]+1)]:
        mutated=signed.copy(); mutated[index]=replacement
        rejected(A, APPROVED, mutated, 'InvalidSignature', mutated[2])
    mutated=signed.copy(); mutated[4]+=1
    rejected(A, APPROVED, mutated, 'EpochMismatch', 2)
    for key,value in [('name','Other'),('version','2'),('chainId',31338),('verifyingContract',P)]:
        domain=dict(name='Leash',version='1',chainId=31337,verifyingContract=V); domain[key]=value
        rejected(A, APPROVED, approval(4,domain=domain), 'InvalidSignature', 4)
    send(O,'cancelApproval(uint256)',2)
    rejected(A,APPROVED,signed,'NonceUsed',2)
    stale=approval(5)
    send(O,'revoke(address)',A); configure()
    rejected(A,APPROVED,stale,'EpochMismatch',5)
    signed=approval(6)
    send(O,'pause()')
    rejected(A,SPEND,[P,1],'Paused')
    rejected(A,APPROVED,signed,'Paused',6)
    # Owner recovery works while paused.
    send(O,'withdraw(address,uint256)',P,1); send(O,'withdraw(address,uint256)',P,1)
    send(O,'unpause()'); send(A,APPROVED,*signed)
    mark('pause preserves approvals; owner recovery while paused')
    signed=approval(7)
    send(O,'revoke(address)',A)
    rejected(A,APPROVED,signed,'UnknownAgent',7)
    configure()
    signed=approval(8,deadline=now()+20)
    warp(signed[3])
    # eth_call at exact deadline succeeds; next block is explicitly same timestamp.
    rpc('eth_call',{'from':A,'to':V,'data':data(APPROVED,*signed)},'latest')
    mark('approval accepted at exact deadline')
    warp(signed[3]+1)
    rejected(A,APPROVED,signed,'ApprovalExpired',8)
    signed=approval(9)
    raw=bytes.fromhex(signed[-1][2:]); r,s,v=int.from_bytes(raw[:32],'big'),int.from_bytes(raw[32:64],'big'),raw[64]
    assert v in (27,28)
    compact=r.to_bytes(32,'big')+(s|((v-27)<<255)).to_bytes(32,'big')
    compact_args=signed.copy(); compact_args[-1]='0x'+compact.hex()
    send(A,APPROVED,*compact_args)
    rejected(A,APPROVED,signed,'NonceUsed',9)
    mark('EIP-2098 compact signature and shared replay identity')
    signed=approval(10); raw=bytes.fromhex(signed[-1][2:])
    bads=['0x','0x00','0x'+'00'*64,'0x'+'00'*65]
    bads += ['0x'+(raw[:64]+bytes([v])).hex() for v in (0,1)]
    order=0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141
    bads += ['0x'+(raw[:32]+(order-int.from_bytes(raw[32:64],'big')).to_bytes(32,'big')+bytes([55-raw[64]])).hex()]
    bads += ['0x'+('00'*32)+raw[32:].hex(), '0x'+raw[:32].hex()+('00'*32)+raw[64:].hex()]
    for bad in bads:
        args=signed.copy(); args[-1]=bad
        rejected(A,APPROVED,args,'InvalidSignature',10)
    # Removing a payee doesn't revoke a previously signed exception.
    configure(allow=True); signed=approval(11)
    send(O,'allowPayee(address,address,bool)',A,P,True)
    send(O,'allowPayee(address,address,bool)',A,P,False)
    send(A,APPROVED,*signed)
    mark('payee removal leaves exact owner approval valid')
    configure()
    zero=approval(12,amount=0)
    rejected(A,APPROVED,zero,'ZeroAmount',12)


def sequential_calls():
    global V
    relay = '0x0000000000000000000000000000000000008001'
    for index, routes in enumerate(((SPEND, SPEND), (APPROVED, APPROVED),
                                    (SPEND, APPROVED), (APPROVED, SPEND))):
        configure(relay)
        end = read('remaining(address)', relay)[1]
        payloads = []
        for step, route in enumerate(routes):
            if route == SPEND:
                payloads.append(data(SPEND, P, step + 1))
            else:
                args = approval(1000 + 2*index + step, amount=step + 1, agent=relay)
                payloads.append(data(APPROVED, *args))
        install_relay(relay, payloads)
        before = balance(V), balance(P)
        receipt = tx(O, relay)
        assert int(receipt['status'], 16) == 1
        results = relay_results(relay, 2)
        label = '/'.join('spend' if route == SPEND else 'approved' for route in routes)
        assert results == [(1, 0), (1, 0)], (f'sequential {label} unlock', results)
        assert (balance(V), balance(P)) == (before[0] - 3, before[1] + 3)
        assert read('remaining(address)', relay) == (22, end)
        assert len(receipt['logs']) == 2
        for step, (route, log) in enumerate(zip(routes, receipt['logs'])):
            if route == SPEND:
                event(log, 'Spent(address,address,uint256,uint256)',
                      [int(relay, 16), int(P, 16), step + 1, end - 3600])
            else:
                nonce = 1000 + 2*index + step
                assert read('nonceUsed(uint256)', nonce) == (1,)
                event(log, 'SpentApproved(address,address,uint256,uint256,uint256)',
                      [int(relay, 16), int(P, 16), step + 1, nonce, end - 3600])
        assert read('nonceUsed(uint256)', 0) == (0,)
        mark(f'two successful {label} calls in one transaction: transfers, budget, nonces, events')

    # A separate vault has the relay as its immutable owner, so both recovery
    # calls are authorized by the actual CALL context in one outer transaction.
    original_vault = V
    bytecode = (ROOT / 'out/LeashVault.bin').read_text().strip().removeprefix('0x')
    deploy = tx(O, payload='0x' + bytecode + relay[2:].zfill(64))
    assert int(deploy['status'], 16) == 1
    try:
        V = deploy['contractAddress']
        send(O, 'fund()', value=20)
        install_relay(relay, [data('withdraw(address,uint256)', P, amount) for amount in (1, 2)])
        before = balance(V), balance(P), read('remaining(address)', A), read('epoch(address)', A)
        receipt = tx(O, relay)
        assert int(receipt['status'], 16) == 1
        results = relay_results(relay, 2)
        assert results == [(1, 0), (1, 0)], ('sequential withdraw unlock', results)
        assert (balance(V), balance(P)) == (before[0] - 3, before[1] + 3)
        assert (read('remaining(address)', A), read('epoch(address)', A)) == before[2:]
        assert read('nonceUsed(uint256)', 0) == (0,)
        assert len(receipt['logs']) == 2
        for log, amount in zip(receipt['logs'], (1, 2)):
            event(log, 'Withdrawn(address,uint256)', [int(P, 16), amount])
        mark('two successful owner withdrawals in one transaction: transfers and unchanged agent accounting')
    finally:
        V = original_vault


def approved_callbacks():
    for kind, inner in enumerate((SPEND, APPROVED)):
        for index, outer in enumerate((SPEND, APPROVED, 'withdraw(address,uint256)')):
            configure(R)
            configure()
            nonce = 1100 + 3*kind + index
            args = approval(nonce, amount=1, agent=R)
            payload = data(inner, P, 1) if inner == SPEND else data(APPROVED, *args)
            install_relay(R, [payload])
            before = balance(V), balance(P), balance(R), read('remaining(address)', R)
            if outer == APPROVED:
                outer_args = approval(1200 + 3*kind + index, amount=1, payee=R)
                receipt = send(A, APPROVED, *outer_args)
                assert read('nonceUsed(uint256)', outer_args[2]) == (1,)
            else:
                receipt = send(O if outer.startswith('withdraw') else A, outer, R, 1)
            results = relay_results(R, 1)
            expected = int(cast('sig', 'Reentrant()'), 16) << 224
            assert results == [(0, expected)], (outer, inner, results)
            assert (balance(V), balance(P), balance(R)) == (before[0] - 1, before[1], before[2] + 1)
            assert read('remaining(address)', R) == before[3]
            assert read('nonceUsed(uint256)', nonce) == (0,)
            assert read('remaining(address)', A)[0] == (25 if outer.startswith('withdraw') else 24)
            assert len(receipt['logs']) == 1
            mark(f'caught {outer.split("(")[0]}-to-{inner.split("(")[0]} callback: exact Reentrant, no inner effects')


def failure_and_callbacks():
    configure(t=MAX,c=MAX)
    rejected(A, SPEND, [P,balance(V)+1], 'InsufficientBalance')
    # Recipient code installed only on the isolated local test chain.
    for address,code in [(R,'0x60006000fd'),(G,'0x5b600056')]:
        rpc('anvil_setCode',address,code)
        rejected(A,SPEND,[address,1],'TransferFailed')
        rejected(O,'withdraw(address,uint256)',[address,1],'TransferFailed')
        args=approval(20 if address==R else 21,payee=address)
        rejected(A,APPROVED,args,'TransferFailed',args[2])
    mark('reverting and gas-burning recipients roll back state, nonces and events')
    # Raw callback performs a fully funded, within-policy spend; catches revert,
    # and records both success and exact returndata selector in storage.
    payload=data(SPEND,P,1)[2:]
    code=''
    for offset in range(0,len(payload),64):
        word=payload[offset:offset+64].ljust(64,'0')
        code+='7f'+word+'60'+f'{offset//2:02x}'+'52'
    # CALL(out=128,size=32,in=0,size=68,value=0,to=vault,gas=200000)
    code+='6020608060446000600073'+V[2:]+'62030d40f160005560805160015500'
    rpc('anvil_setCode',R,'0x'+code)
    configure(R,t=10,c=25)
    configure(t=10,c=25)
    send(A,SPEND,R,1)
    assert int(rpc('eth_getStorageAt',R,'0x0','latest'),16)==0
    stored=int(rpc('eth_getStorageAt',R,'0x1','latest'),16)
    assert stored>>224==int(cast('sig','Reentrant()'),16), hex(stored)
    assert read('remaining(address)',R)[0]==25
    send(O,'withdraw(address,uint256)',R,1)
    assert int(rpc('eth_getStorageAt',R,'0x1','latest'),16)>>224==int(cast('sig','Reentrant()'),16)
    send(A,SPEND,P,1)
    mark('caught cross-agent and withdraw-to-spend reentry hits shared lock')
    # Exact balance success, then empty balance rejection.
    rpc('anvil_setBalance',V,hex(5)); configure(t=10,c=25)
    send(A,SPEND,P,5)
    rejected(A,SPEND,[P,1],'InsufficientBalance')
    send(O,'fund()',value=100_000)
    assert read('remaining(address)',A)[0]==20
    mark('exact balance, empty balance, inbound funds never refund spent')
    # Synthetic full-width balance exercises reachable accounting overflow
    # after a valid successful MAX_AMOUNT exception; no storage is patched.
    z='0x0000000000000000000000000000000000009999'
    rpc('anvil_setBalance',z,'0x0')
    rpc('anvil_setBalance',V,hex(MAX))
    configure(t=MAX,c=MAX)
    send(A,APPROVED,*approval(22,amount=MAX,payee=z))
    rpc('anvil_setBalance',V,'0x1')
    rejected(A,APPROVED,approval(23,amount=1),'AccountingOverflow',23)
    configure()
    send(O,'fund()',value=100_000)
    mark('full-width approved accounting overflow preserves nonce and balance')


def windows_and_oracle():
    configure(t=100,c=100,period=60)
    end=read('remaining(address)',A)[1]
    warp(end-2); send(A,SPEND,P,100)
    warp(end)
    assert read('remaining(address)',A)==(100,end+60)
    send(A,SPEND,P,100)
    mark('documented 2C boundary burst and view-before-spend normalisation')
    warp(end+6000)
    assert read('remaining(address)',A)==(100,end+6060)
    send(A,SPEND,P,1)
    mark('long idle skips 100 windows without loops')
    rpc('anvil_setBalance',V,hex(400)); configure(t=100,c=100,period=60)
    for i in range(4):
        if i: warp(read('remaining(address)',A)[1])
        send(A,SPEND,P,100)
    assert balance(V)==0
    mark('documented four-window vault drain; no lifetime cap')
    send(O,'fund()',value=1_000_000)
    # Independent oracle groups gross successful transfers by floor division
    # against the configuration anchor. Seed is fixed and printed for replay.
    rng=random.Random(1729)
    configure(t=10,c=25,period=3600)
    anchor=read('remaining(address)',A)[1]-3600
    totals={}
    for i in range(60):
        if i in (15,30,45): warp(now()+7200+rng.randrange(100))
        amount=rng.randrange(1,14)
        k=(now()+1-anchor)//3600
        spent=totals.get(k,0)
        expect=amount<=10 and spent+amount<=25
        receipt=tx(A,V,data(SPEND,P,amount))
        assert bool(int(receipt['status'],16))==expect,(i,amount,spent)
        block=rpc('eth_getBlockByNumber',receipt['blockNumber'],False)
        k=(int(block['timestamp'],16)-anchor)//3600
        if expect: totals[k]=totals.get(k,0)+amount
        else: assert receipt['logs']==[]
        assert read('remaining(address)',A)==(25-totals.get(k,0),anchor+(k+1)*3600)
    mark('independent Python oracle: seed 1729, 60 ordinary actions across four windows')


def main():
    global URL,O,A,B,P,R,G,V
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    URL=f'http://127.0.0.1:{port}'
    process=subprocess.Popen([ANVIL,'--silent','--hardfork','osaka','--chain-id','31337','--timestamp','1793664000','--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                accounts=rpc('eth_accounts'); break
            except (OSError,TimeoutError): time.sleep(0.05)
        else: raise RuntimeError('Anvil failed to start')
        O,A,B,P,R,G=accounts[:6]
        bytecode=(ROOT/'out/LeashVault.bin').read_text().strip().removeprefix('0x')
        invalid=tx(O,payload='0x'+bytecode+'00'*32)
        assert int(invalid['status'],16)==0 and invalid['logs']==[]
        mark('zero owner constructor rejected')
        deploy=tx(O,payload='0x'+bytecode+O[2:].zfill(64))
        assert int(deploy['status'],16)==1
        V=deploy['contractAddress']
        send(O,'fund()',value=1_000_000)
        ordinary_and_admin(); payee_removal(); approvals(); sequential_calls()
        approved_callbacks(); failure_and_callbacks(); windows_and_oracle()
        print(f'All {N} integration checks passed; seed 1729, 60 oracle actions.')
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__=='__main__':
    main()
