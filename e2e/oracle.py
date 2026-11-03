"""Independent transition model; window totals come from an unbounded payment journal."""
from dataclasses import dataclass, field

MAX = 2**256 - 1


@dataclass
class Agent:
    tx_cap: int
    window_cap: int
    period: int
    anchor: int
    epoch: int
    allowlist_on: bool
    active: bool = True
    allowed: set = field(default_factory=set)


@dataclass(frozen=True)
class Payment:
    agent: str
    epoch: int
    timestamp: int
    amount: int
    approved: bool


@dataclass
class VaultModel:
    owner: str
    balance: int
    paused: bool = False
    agents: dict = field(default_factory=dict)
    epochs: dict = field(default_factory=dict)
    used_nonces: set = field(default_factory=set)
    payments: list = field(default_factory=list)
    received: dict = field(default_factory=dict)

    def window_payments(self, agent, timestamp):
        record = self.agents[agent]
        window = (timestamp - record.anchor) // record.period
        return [payment for payment in self.payments
                if payment.agent == agent and payment.epoch == record.epoch
                and (payment.timestamp - record.anchor) // record.period == window]

    def remaining(self, agent, timestamp):
        record = self.agents.get(agent)
        if record is None or not record.active:
            return (0, 0)
        window = (timestamp - record.anchor) // record.period
        gross = sum(payment.amount for payment in self.window_payments(agent, timestamp))
        return (max(record.window_cap - gross, 0), record.anchor + (window + 1) * record.period)

    def apply(self, action, timestamp):
        """Return (typed error, expected events), committing only successful actions."""
        kind, caller = action['kind'], action['caller']
        agent = action.get('agent', caller)
        if kind not in ('spend', 'approved') and caller != self.owner:
            return 'Unauthorized', []
        if kind == 'set':
            t, c, period = action['t'], action['c'], action['period']
            if not (0 <= t <= c <= MAX and 60 <= period <= 31_536_000):
                return 'InvalidLimits', []
            epoch = self.epochs.get(agent, 0) + 1
            self.epochs[agent] = epoch
            self.agents[agent] = Agent(t, c, period, timestamp, epoch, action['allow'])
            return None, [('AgentSet(address,uint256,uint256,uint256,uint256,bool)',
                           [int(agent, 16), epoch, t, c, period, int(action['allow'])])]
        if kind == 'revoke':
            epoch = self.epochs.get(agent, 0) + 1
            self.epochs[agent] = epoch
            if agent in self.agents:
                self.agents[agent].active = False
            return None, [('AgentRevoked(address,uint256)', [int(agent, 16), epoch])]
        if kind == 'allow':
            record = self.agents.get(agent)
            if record is None or not record.active:
                return 'UnknownAgent', []
            payee, allowed = action['payee'], action['allowed']
            if allowed:
                record.allowed.add(payee)
            else:
                record.allowed.discard(payee)
            return None, [('PayeeSet(address,uint256,address,bool)',
                           [int(agent, 16), record.epoch, int(payee, 16), int(allowed)])]
        if kind in ('pause', 'unpause'):
            self.paused = kind == 'pause'
            return None, [('PauseSet(bool)', [int(self.paused)])]
        if kind == 'cancel':
            nonce = action['nonce']
            self.used_nonces.add(nonce)
            return None, [('ApprovalCancelled(uint256)', [nonce])]
        if kind == 'withdraw':
            if action['amount'] > self.balance:
                return 'InsufficientBalance', []
            self.balance -= action['amount']
            payee = action['payee']
            self.received[payee] = self.received.get(payee, 0) + action['amount']
            return None, [('Withdrawn(address,uint256)', [int(payee, 16), action['amount']])]
        assert kind in ('spend', 'approved'), kind
        record = self.agents.get(caller)
        if record is None or not record.active:
            return 'UnknownAgent', []
        if self.paused:
            return 'Paused', []
        payments = self.window_payments(caller, timestamp)
        gross = sum(payment.amount for payment in payments)
        amount, payee = action['amount'], action['payee']
        if kind == 'approved':
            if timestamp > action['deadline']:
                return 'ApprovalExpired', []
            if action['epoch'] != self.epochs[caller]:
                return 'EpochMismatch', []
            if action['nonce'] in self.used_nonces:
                return 'NonceUsed', []
            if not action.get('signature_valid', True) or agent != caller:
                return 'InvalidSignature', []
        else:
            if amount > record.tx_cap:
                return 'PerTxExceeded', []
            if gross > record.window_cap or amount > record.window_cap - gross:
                return 'WindowExceeded', []
            if record.allowlist_on and payee not in record.allowed:
                return 'PayeeDenied', []
        if amount > self.balance:
            return 'InsufficientBalance', []
        if amount == 0:
            return 'ZeroAmount', []
        if gross + amount > MAX:
            return 'AccountingOverflow', []
        self.payments.append(Payment(caller, record.epoch, timestamp, amount, kind == 'approved'))
        self.balance -= amount
        self.received[payee] = self.received.get(payee, 0) + amount
        start = record.anchor + ((timestamp - record.anchor) // record.period) * record.period
        words = [int(caller, 16), int(payee, 16), amount]
        if kind == 'approved':
            self.used_nonces.add(action['nonce'])
            words.append(action['nonce'])
        words.append(start)
        # Check the guarantee against journal sums using unbounded integers.
        current = self.window_payments(caller, timestamp)
        approved = sum(payment.amount for payment in current if payment.approved)
        ordinary = sum(payment.amount for payment in current if not payment.approved)
        assert ordinary <= record.window_cap
        assert ordinary + approved == gross + amount <= record.window_cap + approved
        signature = ('SpentApproved(address,address,uint256,uint256,uint256)' if kind == 'approved'
                     else 'Spent(address,address,uint256,uint256)')
        return None, [(signature, words)]
