"""Published, bounded mixed-action corpora replayed against the real vault."""
from collections import Counter
from copy import deepcopy
import json
import random

from oracle import VaultModel

SEEDS = (1729, 104729)
RANDOM_ACTIONS = 60


def run_corpus(h, seed):
    rng = random.Random(seed)
    model = VaultModel(owner=h.O, balance=1_000_000)
    agents = (h.A, h.B)
    payees = ('0x000000000000000000000000000000000000a001',
              '0x000000000000000000000000000000000000a002')
    for payee in payees:
        h.rpc('anvil_setBalance', payee, '0x0')
    trace, tickets, watched_nonces = [], [], {0}
    coverage = Counter()
    next_nonce = 3000

    def nonce():
        nonlocal next_nonce
        next_nonce += 1
        return next_nonce

    def signed(agent=h.A, amount=3, payee=payees[0], **fields):
        action = dict(kind='approved', caller=agent, agent=agent, payee=payee,
                      amount=amount, nonce=fields['nonce'] if 'nonce' in fields else nonce(),
                      epoch=fields.get('epoch', model.epochs.get(agent, 0)),
                      deadline=fields.get('deadline', h.now() + 1800),
                      signature_valid=fields.get('signature_valid', True))
        args = h.approval(action['nonce'], amount=amount, agent=agent, payee=payee,
                          epoch=action['epoch'], deadline=action['deadline'])
        action['signature'] = args[-1] if action['signature_valid'] else '0x'
        tickets.append(action)
        return action

    def state(timestamp):
        assert h.balance(h.V) == model.balance
        assert h.read('paused()') == (int(model.paused),)
        for agent in agents:
            assert h.read('epoch(address)', agent) == (model.epochs.get(agent, 0),)
            assert h.read('remaining(address)', agent) == model.remaining(agent, timestamp)
        for item in watched_nonces:
            assert h.read('nonceUsed(uint256)', item) == (int(item in model.used_nonces),)
        for payee in payees:
            assert h.balance(payee) == model.received.get(payee, 0)

    def execute(action):
        action = action.copy()
        kind = action['kind']
        trace.append({key: value for key, value in action.items() if key != 'signature'})
        try:
            if kind == 'warp':
                h.warp(h.now() + action['seconds'])
                state(h.now())
                coverage['warp'] += 1
                return
            agent = action.get('agent', h.A)
            action.setdefault('caller', agent if kind in ('spend', 'approved') else h.O)
            if kind in ('approved', 'cancel'):
                watched_nonces.add(action['nonce'])
            if kind == 'set':
                signature, args = h.SET, [agent, action['t'], action['c'], action['period'], action['allow']]
            elif kind == 'allow':
                signature, args = 'allowPayee(address,address,bool)', [agent, action['payee'], action['allowed']]
            elif kind == 'revoke':
                signature, args = 'revoke(address)', [agent]
            elif kind == 'cancel':
                signature, args = 'cancelApproval(uint256)', [action['nonce']]
            elif kind in ('pause', 'unpause'):
                signature, args = kind + '()', []
            elif kind in ('spend', 'withdraw'):
                signature = h.SPEND if kind == 'spend' else 'withdraw(address,uint256)'
                args = [action['payee'], action['amount']]
            else:
                assert kind == 'approved'
                signature = h.APPROVED
                args = [action[key] for key in ('payee', 'amount', 'nonce', 'deadline', 'epoch', 'signature')]
            before = deepcopy(model)
            balances = [h.balance(address) for address in (h.V, *payees)]
            receipt = h.tx(action['caller'], h.V, h.data(signature, *args))
            block = h.rpc('eth_getBlockByNumber', receipt['blockNumber'], False)
            timestamp = int(block['timestamp'], 16)
            trace[-1]['timestamp'] = timestamp
            error, events = model.apply(action, timestamp)
            assert int(receipt['status'], 16) == int(error is None), (action, error, receipt)
            if error:
                assert model == before, (action, error, 'model changed on rejection')
                assert receipt['logs'] == []
                assert [h.balance(address) for address in (h.V, *payees)] == balances
                h.mined_error(receipt, error)
                coverage[error] += 1
            else:
                assert len(receipt['logs']) == len(events)
                for log, (topic, words) in zip(receipt['logs'], events):
                    h.event(log, topic, words)
                coverage[kind] += 1
            state(timestamp)
        except Exception as ex:
            raise AssertionError(f'mixed oracle seed={seed}, action={len(trace)-1}, '
                                 f'trace={json.dumps(trace, sort_keys=True)}') from ex

    def configure(agent=h.A, **fields):
        action = dict(kind='set', agent=agent, t=10, c=25, period=3600, allow=False)
        action.update(fields)
        execute(action)

    def spend(agent=h.A, amount=1, payee=payees[0]):
        execute(dict(kind='spend', agent=agent, payee=payee, amount=amount))

    configure()
    configure(h.B)
    ticket = signed(amount=4)
    execute(ticket)
    spend(amount=5)
    execute(ticket)  # successful approval followed by replay
    execute(signed(h.B, nonce=ticket['nonce']))  # global, cross-agent nonce
    execute(signed(amount=30))  # successful above-cap exception
    spend()  # gross ordinary + approved accounting leaves no headroom
    stale = signed()
    configure()  # identical configuration starts a fresh epoch and budget
    execute(stale)
    execute(signed(amount=0))
    execute(signed(signature_valid=False))
    execute(signed(deadline=h.now() - 1))
    execute(signed(amount=1_000_001))
    spend(amount=11)
    spend(amount=0)

    # Each pair uses a live, pre-signed request. The two payment/admin orderings
    # are independent traces; pause and revoke each cover both spending routes.
    for transition, route in (('pause', 'spend'), ('pause', 'approved'),
                              ('revoke', 'spend'), ('revoke', 'approved'),
                              ('remove', 'spend'), ('cancel', 'approved')):
        for payment_first in (True, False):
            execute(dict(kind='unpause'))
            configure(allow=transition == 'remove')
            if transition == 'remove':
                execute(dict(kind='allow', agent=h.A, payee=payees[0], allowed=True))
            payment = signed() if route == 'approved' else dict(kind='spend', agent=h.A, payee=payees[0], amount=1)
            if transition == 'remove':
                admin = dict(kind='allow', agent=h.A, payee=payees[0], allowed=False)
            elif transition == 'cancel':
                admin = dict(kind='cancel', nonce=payment['nonce'])
            else:
                admin = dict(kind=transition, agent=h.A)
            execute(payment if payment_first else admin)
            execute(admin if payment_first else payment)
            if payment_first:
                execute(payment)  # subsequent payment observes the admin change

    execute(dict(kind='unpause'))
    configure(allow=True)
    execute(dict(kind='allow', agent=h.A, payee=payees[0], allowed=True))
    stale = signed()
    configure(allow=True)
    spend()  # epoch-scoped payees do not survive even identical reconfiguration
    execute(stale)
    execute(signed())  # owner exception still bypasses the empty allowlist
    configure()
    configure(h.B)
    execute(dict(kind='withdraw', payee=payees[1], amount=2))
    execute(dict(kind='pause', caller=h.A))
    execute(dict(kind='allow', agent=h.B, payee=payees[1], allowed=True))
    execute(dict(kind='warp', seconds=7201))
    spend()

    kinds = ('spend', 'spend', 'approved', 'approved', 'replay', 'set', 'revoke',
             'allow', 'pause', 'unpause', 'cancel', 'withdraw', 'warp')
    for _ in range(RANDOM_ACTIONS):
        kind, agent, payee = rng.choice(kinds), rng.choice(agents), rng.choice(payees)
        caller = h.O if rng.randrange(5) else h.B
        if kind == 'spend':
            spend(agent, rng.choice((0, 1, 5, 10, 11)), payee)
        elif kind == 'approved':
            execute(signed(agent, rng.choice((0, 1, 4, 30)), payee,
                           deadline=h.now() + rng.choice((-1, 1800, 1800)),
                           signature_valid=rng.randrange(5) != 0))
        elif kind == 'replay':
            execute(rng.choice(tickets))
        elif kind == 'set':
            configure(agent, t=rng.choice((1, 5, 10)), c=rng.choice((10, 25, 40)),
                      period=rng.choice((60, 3600)), allow=bool(rng.randrange(2)), caller=caller)
        elif kind == 'allow':
            execute(dict(kind=kind, agent=agent, payee=payee, allowed=bool(rng.randrange(2)), caller=caller))
        elif kind == 'cancel':
            execute(dict(kind=kind, nonce=rng.choice(tickets)['nonce'], caller=caller))
        elif kind == 'withdraw':
            execute(dict(kind=kind, payee=payee, amount=rng.choice((1, 3)), caller=caller))
        elif kind == 'warp':
            execute(dict(kind=kind, seconds=rng.choice((61, 3601, 7201))))
        else:
            execute(dict(kind=kind, agent=agent, caller=caller))
    required = {'spend', 'approved', 'set', 'allow', 'revoke', 'pause', 'unpause', 'cancel',
                'withdraw', 'warp', 'NonceUsed', 'EpochMismatch', 'InvalidSignature',
                'ApprovalExpired', 'ZeroAmount', 'InsufficientBalance', 'PerTxExceeded',
                'WindowExceeded', 'PayeeDenied', 'Paused', 'UnknownAgent', 'Unauthorized'}
    assert required <= coverage.keys(), (seed, required - coverage.keys())
    h.mark(f'mixed journal oracle: seed {seed}, {len(trace)} actions '
           f'({len(trace) - coverage["warp"]} transactions, {coverage["warp"]} time jumps), '
           f'{sum(coverage[error] for error in required if error[0].isupper())} exact mined rejections')
    return len(trace)
