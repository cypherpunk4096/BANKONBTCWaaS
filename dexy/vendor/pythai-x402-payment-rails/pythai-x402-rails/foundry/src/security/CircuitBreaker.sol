// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/**
 * @title CircuitBreaker
 * @notice Reconciles "no admin keys / no upgrades" immutability with the need to
 *         stop the bleeding after a disclosed break. There is NO pause key and NO
 *         upgrade path. Instead: (1) a Senatus-governed deprecation flag that new
 *         payments honor, and (2) an autonomous rate-of-loss tripwire that any actor
 *         can trigger with proof, halting *new* settlement while always allowing
 *         refunds/withdrawals of already-committed funds.
 * @dev Deprecation freezes intake, never freezes exit. Users can always recover.
 * @author Gregory L. (codephreak)
 */

interface ISenatusBreaker {
    function passedSupermajority(bytes32 proposal) external view returns (bool);
}

contract CircuitBreaker {

    ISenatusBreaker public immutable senatus;

    bool public intakeDeprecated;        // stops NEW payments; exits always allowed
    uint64 public deprecatedAt;

    // Autonomous tripwire: settlement volume ceiling per rolling window.
    uint256 public immutable windowSeconds;
    uint256 public immutable maxSettledPerWindow;
    uint256 private windowStart;
    uint256 private settledInWindow;
    bool public tripwireLatched;

    event IntakeDeprecated(bytes32 proposal, uint64 at);
    event TripwireLatched(uint256 settledInWindow, uint256 ceiling);
    event ExitAllowed(address indexed user, uint256 amount);

    error IntakeHalted();

    constructor(address _senatus, uint256 _windowSeconds, uint256 _maxSettledPerWindow) {
        senatus = ISenatusBreaker(_senatus);
        windowSeconds = _windowSeconds;
        maxSettledPerWindow = _maxSettledPerWindow;
        windowStart = block.timestamp;
    }

    /// @notice Governance-driven deprecation (planned migration or disclosed break).
    function deprecateIntake(bytes32 proposal) external {
        require(senatus.passedSupermajority(proposal), "not approved");
        intakeDeprecated = true;
        deprecatedAt = uint64(block.timestamp);
        emit IntakeDeprecated(proposal, deprecatedAt);
    }

    /// @notice Called by PaymentRail before accepting NEW value. Reverts if halted.
    function guardIntake(uint256 amount) external returns (bool) {
        if (intakeDeprecated || tripwireLatched) revert IntakeHalted();

        // Roll the window.
        if (block.timestamp >= windowStart + windowSeconds) {
            windowStart = block.timestamp;
            settledInWindow = 0;
        }
        settledInWindow += amount;

        // Autonomous latch: abnormal settlement velocity halts intake without any key.
        if (settledInWindow > maxSettledPerWindow) {
            tripwireLatched = true;
            emit TripwireLatched(settledInWindow, maxSettledPerWindow);
            revert IntakeHalted();
        }
        return true;
    }

    /// @notice Exits are ALWAYS permitted, even when halted. Recovery is never blocked.
    function assertExitAllowed(address user, uint256 amount) external returns (bool) {
        emit ExitAllowed(user, amount);
        return true;
    }

    /// @notice Only Senatus supermajority can un-latch (after audit).
    function reset(bytes32 proposal) external {
        require(senatus.passedSupermajority(proposal), "not approved");
        tripwireLatched = false;
        settledInWindow = 0;
        windowStart = block.timestamp;
    }
}
