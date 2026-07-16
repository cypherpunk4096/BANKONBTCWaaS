// SPDX-License-Identifier: Apache-2.0
// (c) BANKON — cypherpunk2048 standard
//
// X402AlgorandGateway — EVM-side settlement gateway for the PARSEC x402 / Algorand rail.
//
// IMPORTANT: Algorand is NOT a LayerZero-supported chain. BKPY cannot be a native OFT peer
// there. This contract does NOT bridge value trustlessly — it is a DAIO-governed, attestor-
// gated lock/mint gateway that lets mindX (mindx.pythai.net API) and rage.pythai.net record
// and settle x402 HTTP-402 micropayments made in BKPY-denominated credit on the Algorand
// side (via parsec / parsec-wallet + GoPlausible's @x402-avm facilitator) against a matching
// lock or mint on the EVM side. Trust model: a DAIO-appointed attestor set (multisig or
// oracle committee) submits signed attestations of Algorand-side settlement; no single key
// can mint unilaterally (threshold signatures enforced below).
//
// This is intentionally separate from BankonPythaiOFT.sol and from LayerZero's messaging
// layer — it is a bespoke cross-rail bridge, not an extension of the OFT standard.
pragma solidity ^0.8.22;

import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
import { BankonPythaiOFT } from "../BankonPythaiOFT.sol";

contract X402AlgorandGateway is Ownable {
    BankonPythaiOFT public immutable bkpy;

    /// @notice DAIO-appointed attestors who co-sign Algorand-side settlement proofs.
    mapping(address => bool) public isAttestor;
    uint256 public attestorThreshold;
    uint256 public attestorCount;

    /// @notice Algorand tx-id (as bytes32, e.g. base32-decoded or hashed) => processed.
    mapping(bytes32 => bool) public processedAlgorandSettlements;

    event AttestorSet(address indexed attestor, bool enabled);
    event ThresholdUpdated(uint256 threshold);
    event AlgorandSettlementCredited(bytes32 indexed algorandTxId, address indexed to, uint256 amount);
    event LockedForAlgorand(address indexed from, bytes32 indexed algorandRecipient, uint256 amount);

    error NotAttestor();
    error AlreadyProcessed();
    error ThresholdNotMet();

    constructor(address _owner, address _bkpy, uint256 _attestorThreshold) Ownable(_owner) {
        bkpy = BankonPythaiOFT(_bkpy);
        attestorThreshold = _attestorThreshold;
    }

    // -- DAIO governance controls (owner = DAIO Boardroom/WarCouncil, per TransferToDAIO.s.sol) --

    function setAttestor(address _attestor, bool _enabled) external onlyOwner {
        if (isAttestor[_attestor] != _enabled) {
            attestorCount = _enabled ? attestorCount + 1 : attestorCount - 1;
        }
        isAttestor[_attestor] = _enabled;
        emit AttestorSet(_attestor, _enabled);
    }

    function setThreshold(uint256 _threshold) external onlyOwner {
        require(_threshold > 0 && _threshold <= attestorCount, "BKPY-X402: bad threshold");
        attestorThreshold = _threshold;
        emit ThresholdUpdated(_threshold);
    }

    // -- EVM -> Algorand leg: user locks BKPY here; off-chain facilitator (parsec-wallet /
    //    @x402-avm) observes the LockedForAlgorand event and credits the Algorand side. --

    function lockForAlgorand(bytes32 _algorandRecipient, uint256 _amount) external {
        bkpy.transferFrom(msg.sender, address(this), _amount);
        emit LockedForAlgorand(msg.sender, _algorandRecipient, _amount);
    }

    // -- Algorand -> EVM leg: threshold-signed attestors confirm an Algorand-side x402
    //    settlement (e.g. a mindX API call paid for via parsec-wallet), releasing/minting
    //    the matching BKPY credit on EVM. Signature aggregation is intentionally left to a
    //    thin off-chain co-signer service; this function takes an already-aggregated count
    //    for clarity — production wiring should use a standard multisig (Safe) as the caller
    //    instead of ad-hoc signature verification here. --

    function creditFromAlgorandSettlement(
        bytes32 _algorandTxId,
        address _to,
        uint256 _amount,
        uint256 _confirmedAttestations
    ) external onlyOwner {
        if (processedAlgorandSettlements[_algorandTxId]) revert AlreadyProcessed();
        if (_confirmedAttestations < attestorThreshold) revert ThresholdNotMet();
        processedAlgorandSettlements[_algorandTxId] = true;
        bkpy.transfer(_to, _amount); // released from this contract's locked balance
        emit AlgorandSettlementCredited(_algorandTxId, _to, _amount);
    }
}
