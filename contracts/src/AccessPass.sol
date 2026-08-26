// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @title AccessPass
/// @notice Sells time-limited access passes for ETH. Holding a live pass is a
///         necessary condition for reading protected content off-chain — never a
///         sufficient one, because this contract cannot tell an API who is making
///         an HTTP request. That proof is the API's job.
/// @dev Deliberately small. Every line here should survive being asked "why?".
contract AccessPass {
    /// @notice The account allowed to withdraw takings.
    address public immutable owner;

    /// @notice Exact price of one pass, in wei.
    /// @dev Immutable, along with `duration`, so the terms cannot change under a
    ///      buyer after they have read them. It also removes a race that a setter
    ///      would introduce: a purchase in flight when the price moved would revert
    ///      through no fault of the buyer. Changing the terms means a new deployment.
    uint256 public immutable price;

    /// @notice How long one purchase grants access for, in seconds.
    uint256 public immutable duration;

    /// @notice Unix timestamp at which an address's access lapses. Zero means it
    ///         has never held a pass.
    mapping(address => uint256) public expiresAt;

    event PassPurchased(address indexed holder, uint256 expiresAt, uint256 paid);
    event Withdrawn(address indexed to, uint256 amount);

    error IncorrectPayment(uint256 sent, uint256 required);
    error NotOwner();
    error ZeroDuration();
    error ZeroAddress();
    error NothingToWithdraw();
    error TransferFailed();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @param _price Exact wei required per purchase. Zero is permitted — a free
    ///        pass is still a pass, and the access control is what is being tested.
    /// @param _duration Seconds of access granted per purchase. Must be non-zero;
    ///        a zero duration would mint passes that have already expired.
    constructor(uint256 _price, uint256 _duration) {
        if (_duration == 0) revert ZeroDuration();
        owner = msg.sender;
        price = _price;
        duration = _duration;
    }

    /// @notice Buy a pass, or extend one you already hold.
    /// @dev Exact payment is required rather than "at least". Accepting an overpayment
    ///      would oblige this contract to refund the difference, and that refund is an
    ///      external call to an address the caller chose — the classic reentrancy
    ///      surface. Demanding the exact amount deletes the refund path entirely.
    function buyPass() external payable {
        if (msg.value != price) revert IncorrectPayment(msg.value, price);

        // Extend from the existing expiry rather than from now, so renewing early
        // costs the holder nothing. Renewing after expiry starts afresh.
        uint256 current = expiresAt[msg.sender];
        // The same few-second validator tolerance as hasValidPass applies here, and it
        // can only shift an expiry by that much.
        // forge-lint: disable-next-line(block-timestamp)
        uint256 base = current > block.timestamp ? current : block.timestamp;

        uint256 newExpiry = base + duration;
        expiresAt[msg.sender] = newExpiry;

        emit PassPurchased(msg.sender, newExpiry, msg.value);
    }

    /// @notice Whether `who` currently holds a live pass.
    /// @dev A validator can nudge `block.timestamp` by a few seconds. At a duration
    ///      measured in days that is not worth defending against; it would matter if
    ///      passes were ever sold by the second.
    function hasValidPass(address who) external view returns (bool) {
        // forge-lint: disable-next-line(block-timestamp) — accepted: see the note above.
        return expiresAt[who] > block.timestamp;
    }

    /// @notice Send the contract's entire balance to `to`.
    /// @dev Checks-effects-interactions, even though `onlyOwner` already narrows who
    ///      can reach the external call. There is no mutable state to clear here, so
    ///      the ordering is only the event — but the habit is the point, and the next
    ///      version of this function may not be so simple.
    function withdraw(address payable to) external onlyOwner {
        if (to == address(0)) revert ZeroAddress();

        uint256 amount = address(this).balance;
        if (amount == 0) revert NothingToWithdraw();

        emit Withdrawn(to, amount);

        (bool ok,) = to.call{value: amount}("");
        if (!ok) revert TransferFailed();
    }

    // Deliberately no receive() or fallback(). ETH arrives through buyPass() or it
    // does not arrive: a plain transfer would be money this contract took without
    // granting anything, and reverting it is kinder than keeping it.
}
