// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// Matched bare-uint baseline for src/vault.fe; see LAYOUT.md and README.md.
contract LeashVault {
    struct AgentRecord {
        uint256 txCap;
        uint256 windowCap;
        uint256 period;
        uint256 start;
        uint256 spent;
        bool active;
        bool allowlistOn;
    }

    address private immutable vaultOwner;
    mapping(address => AgentRecord) private agents; // root 0
    mapping(address => uint256) private epochs; // root 1
    mapping(bytes32 => bool) private payees; // root 2; direct prehashed slots below
    mapping(uint256 => bool) private usedNonces; // root 3
    bool private isPaused; // slot 4
    uint256 private guardValue; // slot 5, reserved like Fe's unused Mutex.value
    // Fe's Mutex.lock is transient slot 6. All three transfer routes share it.

    uint256 private constant MAX_PERIOD = 31_536_000;
    uint256 private constant MAX_TIMESTAMP = type(uint256).max - MAX_PERIOD;
    uint256 private constant ORDER =
        0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141;
    bytes32 private constant APPROVAL_TYPEHASH = keccak256(
        "OverCapApproval(address agent,address payee,uint256 amount,uint256 nonce,uint256 deadline,uint256 epoch)"
    );
    bytes32 private constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );

    error Unauthorized();
    error InvalidOwner();
    error InvalidLimits();
    error TimeOutOfRange();
    error UnknownAgent();
    error Paused();
    error Reentrant();
    error PerTxExceeded();
    error WindowExceeded();
    error PayeeDenied();
    error ZeroAmount();
    error InsufficientBalance();
    error TransferFailed();
    error AccountingOverflow();
    error EpochOverflow();
    error ApprovalExpired();
    error NonceUsed();
    error InvalidSignature();
    error EpochMismatch();

    event Spent(address agent, address payee, uint256 amount, uint256 window_start);
    event SpentApproved(address agent, address payee, uint256 amount, uint256 nonce, uint256 window_start);
    event AgentSet(address agent, uint256 epoch, uint256 tx_cap, uint256 window_cap, uint256 period, bool allowlist_on);
    event AgentRevoked(address agent, uint256 epoch);
    event PayeeSet(address agent, uint256 epoch, address payee, bool allowed);
    event PauseSet(bool paused);
    event ApprovalCancelled(uint256 nonce);
    event Withdrawn(address to, uint256 amount);
    event Funded(address sender, uint256 amount);

    constructor(address owner_) {
        if (owner_ == address(0)) revert InvalidOwner();
        vaultOwner = owner_;
    }

    function owner() external view returns (address) { return vaultOwner; }
    function paused() external view returns (bool) { return isPaused; }
    function epoch(address agent) external view returns (uint256) { return epochs[agent]; }
    function nonceUsed(uint256 nonce) external view returns (bool) { return usedNonces[nonce]; }
    function fund() external payable { emit Funded(msg.sender, msg.value); }

    function setAgent(address agent, uint256 txCap, uint256 windowCap, uint256 period, bool allowlistOn) external {
        requireOwner();
        requireTime();
        if (period < 60 || period > MAX_PERIOD || txCap > windowCap) revert InvalidLimits();
        uint256 next = nextEpoch(epochs[agent]);
        epochs[agent] = next;
        agents[agent] = AgentRecord(txCap, windowCap, period, block.timestamp, 0, true, allowlistOn);
        emit AgentSet(agent, next, txCap, windowCap, period, allowlistOn);
    }

    function allowPayee(address agent, address payee, bool allowed) external {
        requireOwner();
        if (!agents[agent].active) revert UnknownAgent();
        uint256 current = epochs[agent];
        bytes32 slot = payeeSlot(agent, current, payee);
        assembly ("memory-safe") { sstore(slot, allowed) }
        emit PayeeSet(agent, current, payee, allowed);
    }

    function revoke(address agent) external {
        requireOwner();
        uint256 next = nextEpoch(epochs[agent]);
        epochs[agent] = next;
        agents[agent].active = false;
        emit AgentRevoked(agent, next);
    }

    function pause() external { requireOwner(); isPaused = true; emit PauseSet(true); }
    function unpause() external { requireOwner(); isPaused = false; emit PauseSet(false); }
    function cancelApproval(uint256 nonce) external {
        requireOwner();
        usedNonces[nonce] = true;
        emit ApprovalCancelled(nonce);
    }

    function remaining(address agent) external view returns (uint256, uint256) {
        AgentRecord memory record = agents[agent];
        if (!record.active) return (0, 0);
        requireTime();
        (uint256 start, uint256 spent) = normalise(record);
        return (record.windowCap >= spent ? record.windowCap - spent : 0, start + record.period);
    }

    function spend(address payee, uint256 amount) external {
        AgentRecord memory record = agents[msg.sender];
        requireActive(record);
        lock();
        requireTime();
        (uint256 start, uint256 spent) = normalise(record);
        // Match the tuple-map lookup, including when allowlisting is disabled.
        bytes32 slot = payeeSlot(msg.sender, epochs[msg.sender], payee);
        bool allowed;
        assembly ("memory-safe") { allowed := iszero(iszero(sload(slot))) }
        if (amount > record.txCap) revert PerTxExceeded();
        if (spent > record.windowCap || amount > record.windowCap - spent) revert WindowExceeded();
        if (record.allowlistOn && !allowed) revert PayeeDenied();
        if (address(this).balance < amount) revert InsufficientBalance();
        record.start = start;
        record.spent = checkedSpent(spent, amount);
        agents[msg.sender] = record;
        emit Spent(msg.sender, payee, amount, start);
        sendValue(payee, amount);
        unlock();
    }

    function spendApproved(
        address payee, uint256 amount, uint256 nonce, uint256 deadline, uint256 currentEpoch, bytes calldata signature
    ) external {
        AgentRecord memory record = agents[msg.sender];
        requireActive(record);
        lock();
        requireTime();
        (uint256 start, uint256 spent) = normalise(record);
        if (block.timestamp > deadline) revert ApprovalExpired();
        if (currentEpoch != epochs[msg.sender]) revert EpochMismatch();
        if (usedNonces[nonce]) revert NonceUsed();
        bytes32 domain = keccak256(abi.encode(
            DOMAIN_TYPEHASH, keccak256("Leash"), keccak256("1"), block.chainid, address(this)
        ));
        bytes32 structHash = keccak256(abi.encode(
            APPROVAL_TYPEHASH, msg.sender, payee, amount, nonce, deadline, currentEpoch
        ));
        bytes32 digest = keccak256(abi.encodePacked(hex"1901", domain, structHash));
        if (recover(digest, signature) != vaultOwner) revert InvalidSignature();
        if (address(this).balance < amount) revert InsufficientBalance();
        record.start = start;
        record.spent = checkedSpent(spent, amount);
        agents[msg.sender] = record;
        usedNonces[nonce] = true;
        emit SpentApproved(msg.sender, payee, amount, nonce, start);
        sendValue(payee, amount);
        unlock();
    }

    function withdraw(address to, uint256 amount) external {
        requireOwner();
        lock();
        if (address(this).balance < amount) revert InsufficientBalance();
        emit Withdrawn(to, amount);
        sendValue(to, amount);
        unlock();
    }

    function requireOwner() private view { if (msg.sender != vaultOwner) revert Unauthorized(); }
    function requireTime() private view { if (block.timestamp > MAX_TIMESTAMP) revert TimeOutOfRange(); }
    function nextEpoch(uint256 current) private pure returns (uint256) {
        if (current == type(uint256).max) revert EpochOverflow();
        return current + 1;
    }
    function requireActive(AgentRecord memory record) private view {
        if (!record.active) revert UnknownAgent();
        if (isPaused) revert Paused();
    }
    function checkedSpent(uint256 spent, uint256 amount) private pure returns (uint256) {
        if (amount == 0) revert ZeroAmount();
        if (amount > type(uint256).max - spent) revert AccountingOverflow();
        return spent + amount;
    }
    function normalise(AgentRecord memory record) private view returns (uint256, uint256) {
        assert(record.period >= 60 && record.period <= MAX_PERIOD);
        assert(record.start <= block.timestamp && block.timestamp <= MAX_TIMESTAMP);
        uint256 elapsed = block.timestamp - record.start;
        if (elapsed >= record.period) return (record.start + record.period * (elapsed / record.period), 0);
        return (record.start, record.spent);
    }
    function payeeSlot(address agent, uint256 current, address payee) private pure returns (bytes32) {
        uint256 root;
        assembly ("memory-safe") { root := payees.slot }
        // One keccak over four ABI words, exactly like Fe's StorageMap tuple.
        return keccak256(abi.encode(agent, current, payee, root));
    }
    function recover(bytes32 digest, bytes calldata signature) private pure returns (address signer) {
        bytes32 r;
        bytes32 s;
        uint256 v;
        if (signature.length != 64 && signature.length != 65) revert InvalidSignature();
        assembly ("memory-safe") {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
        }
        if (signature.length == 65) {
            v = uint8(signature[64]);
        } else {
            v = (uint256(s) >> 255) + 27;
            s = bytes32(uint256(s) & (type(uint256).max >> 1));
        }
        if ((v != 27 && v != 28) || uint256(r) == 0 || uint256(r) >= ORDER || uint256(s) == 0 || uint256(s) > ORDER / 2) {
            revert InvalidSignature();
        }
        signer = ecrecover(digest, uint8(v), r, s);
        if (signer == address(0)) revert InvalidSignature();
    }
    function lock() private {
        bool held;
        assembly ("memory-safe") { held := iszero(iszero(tload(6))) }
        if (held) revert Reentrant();
        assembly ("memory-safe") { tstore(6, 1) }
    }
    function unlock() private { assembly ("memory-safe") { tstore(6, 0) } }
    function sendValue(address to, uint256 amount) private {
        bool success;
        // Empty input/output, no returndata copy, gas() subject to EIP-150.
        assembly ("memory-safe") { success := call(gas(), to, amount, 0, 0, 0, 0) }
        if (!success) revert TransferFailed();
    }
}
