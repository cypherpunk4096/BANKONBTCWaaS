// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.26;

/// ---------------------------------------------------------------------------
/// SPINTRADE LISTING DESK — the receiver on the SPINTRADE side of the PLR
/// first-notification channel. When a new chain buys into the layer, the
/// LogoRegistry pushes onChainOnboarded() inside the onboarding transaction;
/// this desk records the intelligence and queues an auto-listing ticket for
/// the SPINTRADE keeper (mindX) to execute — pool creation, initial liquidity
/// routing, NFT LP position mint — before any external indexer has moved.
///
/// cypherpunk2048: no proxy, no admin key. The registry address and keeper
/// are immutable; a new registry or keeper means a new desk.
/// ---------------------------------------------------------------------------

contract SpintradeDesk {
    struct Ticket {
        uint64 chainId;
        string chainName;
        address registrar;
        uint64 receivedAt;
        bool executed;
    }

    /// The only address permitted to file notifications.
    address public immutable PLR;

    /// The SPINTRADE keeper (mindX signer) that consumes tickets.
    address public immutable KEEPER;

    Ticket[] public tickets;
    /// chainId => 1-based ticket index (0 = none)
    mapping(uint64 => uint256) public ticketFor;

    event ListingQueued(uint256 indexed ticketId, uint64 indexed chainId, string chainName);
    event ListingExecuted(uint256 indexed ticketId, uint64 indexed chainId);

    error NotRegistry();
    error NotKeeper();
    error UnknownTicket();
    error AlreadyExecuted();

    constructor(address plr, address keeper) {
        PLR = plr;
        KEEPER = keeper;
    }

    /// Called by LogoRegistry inside the onboarding transaction. Bounded to
    /// 200k gas by the caller, so this stays deliberately lean: store, index,
    /// emit. All heavy lifting happens in the keeper's execution pass.
    function onChainOnboarded(uint64 chainId, string calldata chainName, address registrar)
        external
    {
        if (msg.sender != PLR) revert NotRegistry();
        if (ticketFor[chainId] != 0) return; // idempotent; never revert the layer

        tickets.push(Ticket({
            chainId: chainId,
            chainName: chainName,
            registrar: registrar,
            receivedAt: uint64(block.timestamp),
            executed: false
        }));
        ticketFor[chainId] = tickets.length;
        emit ListingQueued(tickets.length - 1, chainId, chainName);
    }

    /// Keeper marks a ticket executed after pool deployment on the new chain.
    function markExecuted(uint256 ticketId) external {
        if (msg.sender != KEEPER) revert NotKeeper();
        if (ticketId >= tickets.length) revert UnknownTicket();
        Ticket storage t = tickets[ticketId];
        if (t.executed) revert AlreadyExecuted();
        t.executed = true;
        emit ListingExecuted(ticketId, t.chainId);
    }

    function pendingCount() external view returns (uint256 n) {
        for (uint256 i; i < tickets.length; ++i) {
            if (!tickets[i].executed) n++;
        }
    }

    function ticketCount() external view returns (uint256) {
        return tickets.length;
    }
}
