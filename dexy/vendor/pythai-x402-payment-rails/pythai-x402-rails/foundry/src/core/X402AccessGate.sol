// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/**
 * @title X402AccessGate
 * @notice RFC 9110 HTTP 402 Payment Required access control for DAIO
 * @author Gregory L. (codephreak)
 * @dev Integrates with Senatus governance, DebasementIndexV2 fee model
 */

import "@openzeppelin/contracts/access/Ownable.sol";

interface IPaymentRail {
    function verifyPayment(bytes32 receiptId) external view returns (bool);
    function checkRateLimit(address user) external returns (uint256);
}

interface ISenatus {
    function isSenator(address account) external view returns (bool);
    function isDelegate(address account) external view returns (bool);
    function checkRole(address account, bytes32 role) external view returns (bool);
}

contract X402AccessGate is Ownable {
    
    // ========== Roles (DAIO) ==========
    
    bytes32 public constant AUDITOR_ROLE = keccak256("AUDITOR");
    bytes32 public constant DEPLOYER_ROLE = keccak256("DEPLOYER");
    bytes32 public constant AGENT_OPERATOR_ROLE = keccak256("AGENT_OPERATOR");
    bytes32 public constant SENTINEL_ROLE = keccak256("SENTINEL");
    
    // ========== Storage ==========
    
    IPaymentRail public paymentRail;
    ISenatus public senatus;
    
    // Resource -> required role (bytes32(0) = public)
    mapping(bytes32 => bytes32) public resourceRoles;
    mapping(bytes32 => uint256) public resourceFees;
    mapping(bytes32 => bool) public resourceActive;
    
    // User -> resource -> allowed
    mapping(address => mapping(bytes32 => bool)) public userAccess;
    mapping(address => mapping(bytes32 => uint256)) public accessExpiresAt;
    
    // Dispute tracking
    mapping(bytes32 => Dispute) public disputes;
    
    struct Dispute {
        address disputer;
        string reason;
        uint256 initiatedAt;
        bool resolved;
        bool refundApproved;
    }
    
    // ========== Events ==========
    
    event AccessGranted(
        address indexed user,
        bytes32 indexed resource,
        uint256 expiresAt
    );
    
    event AccessDenied(
        address indexed user,
        bytes32 indexed resource,
        string reason
    );
    
    event PaymentRequired(
        bytes32 indexed resource,
        address indexed user,
        uint256 fee,
        bytes32 receiptId
    );
    
    event RoleGatePassed(
        address indexed user,
        bytes32 indexed resource,
        bytes32 role
    );
    
    event RoleGateFailed(
        address indexed user,
        bytes32 indexed resource,
        bytes32 requiredRole,
        string reason
    );
    
    event ResourceRegistered(
        bytes32 indexed resource,
        bytes32 role,
        uint256 fee
    );
    
    event DisputeResolved(
        bytes32 indexed resourceId,
        bool refundApproved
    );
    
    // ========== Constructor ==========
    
    constructor(
        address _paymentRail,
        address _senatus
    ) {
        require(_paymentRail != address(0), "Invalid payment rail");
        require(_senatus != address(0), "Invalid senatus");
        
        paymentRail = IPaymentRail(_paymentRail);
        senatus = ISenatus(_senatus);
    }
    
    // ========== Resource Management ==========
    
    /**
     * @notice Register x402-gated resource with DAIO role requirement
     * @param _resource Resource identifier hash
     * @param _role Required DAIO role (bytes32(0) = public)
     * @param _fee Fee in wei/microAlgo
     */
    function registerResource(
        bytes32 _resource,
        bytes32 _role,
        uint256 _fee
    ) external onlyOwner {
        resourceRoles[_resource] = _role;
        resourceFees[_resource] = _fee;
        resourceActive[_resource] = true;
        
        emit ResourceRegistered(_resource, _role, _fee);
    }
    
    /**
     * @notice Deactivate resource (soft delete)
     */
    function deactivateResource(bytes32 _resource) external onlyOwner {
        resourceActive[_resource] = false;
    }
    
    // ========== HTTP 402 Access Control ==========
    
    /**
     * @notice Check if user has access to resource
     * @param _user User address
     * @param _resource Resource identifier
     * @return allowed True if access granted
     * @return requiresPayment True if 402 Payment Required should be returned
     * @return feeAmount Fee amount if payment required
     */
    function checkAccess(
        address _user,
        bytes32 _resource
    ) external view returns (
        bool allowed,
        bool requiresPayment,
        uint256 feeAmount
    ) {
        require(resourceActive[_resource], "Resource not found");
        
        // Check if user already has valid access token
        if (userAccess[_user][_resource] && 
            block.timestamp <= accessExpiresAt[_user][_resource]) {
            return (true, false, 0);
        }
        
        // Check role-based access
        bytes32 requiredRole = resourceRoles[_resource];
        
        if (requiredRole == bytes32(0)) {
            // Public resource
            return (true, false, 0);
        }
        
        // Check Senatus delegation (overrides role check)
        if (senatus.isDelegate(_user)) {
            return (true, false, 0);
        }
        
        // Check if user has required role
        if (senatus.checkRole(_user, requiredRole)) {
            return (true, false, 0);
        }
        
        // Payment required
        uint256 fee = resourceFees[_resource];
        return (false, true, fee);
    }
    
    /**
     * @notice Generate HTTP 402 response headers
     * @param _resource Resource identifier
     * @param _user User address
     * @return headers JSON-serializable headers
     */
    function get402Headers(
        bytes32 _resource,
        address _user
    ) external view returns (string memory) {
        uint256 fee = resourceFees[_resource];
        
        return string(
            abi.encodePacked(
                '{"WWW-Authenticate":"x402; realm=\\"pythai\\"; fee=',
                _uint2str(fee),
                '","X-PYTHAI-Fee":"',
                _uint2str(fee),
                '","X-PYTHAI-Rate-Limit":"1000","X-PYTHAI-User":"',
                _addr2str(_user),
                '"}'
            )
        );
    }
    
    /**
     * @notice Grant access after successful payment
     * @param _user User address
     * @param _resource Resource identifier
     * @param _durationSeconds Access duration
     * @param _receiptId Payment receipt identifier
     */
    function grantAccess(
        address _user,
        bytes32 _resource,
        uint256 _durationSeconds,
        bytes32 _receiptId
    ) external {
        require(msg.sender == address(paymentRail), "PaymentRail only");
        require(resourceActive[_resource], "Resource not active");
        
        // Verify payment was settled
        bool paymentValid = paymentRail.verifyPayment(_receiptId);
        require(paymentValid, "Payment not verified");
        
        uint256 expiresAt = block.timestamp + _durationSeconds;
        
        userAccess[_user][_resource] = true;
        accessExpiresAt[_user][_resource] = expiresAt;
        
        emit AccessGranted(_user, _resource, expiresAt);
    }
    
    /**
     * @notice Revoke access (emergency or policy enforcement)
     */
    function revokeAccess(address _user, bytes32 _resource) external onlyOwner {
        userAccess[_user][_resource] = false;
        accessExpiresAt[_user][_resource] = 0;
        
        emit AccessDenied(_user, _resource, "Revoked by owner");
    }
    
    // ========== Role-Based Gating ==========
    
    /**
     * @notice Validate role requirement (Senatus integration)
     * @param _user User address
     * @param _requiredRole Role identifier
     * @return valid True if user has role or is senator/delegate
     */
    function validateRoleGate(
        address _user,
        bytes32 _requiredRole
    ) external view returns (bool) {
        // Senators bypass all role checks
        if (senatus.isSenator(_user)) {
            return true;
        }
        
        // Delegates bypass role checks
        if (senatus.isDelegate(_user)) {
            return true;
        }
        
        // Check if user has required role
        return senatus.checkRole(_user, _requiredRole);
    }
    
    /**
     * @notice Special gate for deployer operations (DEPLOYER_ROLE)
     * @param _user User address
     */
    function checkDeployerAccess(address _user) external view returns (bool) {
        if (senatus.isSenator(_user)) return true;
        if (senatus.isDelegate(_user)) return true;
        return senatus.checkRole(_user, DEPLOYER_ROLE);
    }
    
    /**
     * @notice Special gate for agent operations (AGENT_OPERATOR_ROLE)
     */
    function checkAgentOperatorAccess(address _user) external view returns (bool) {
        if (senatus.isSenator(_user)) return true;
        if (senatus.isDelegate(_user)) return true;
        return senatus.checkRole(_user, AGENT_OPERATOR_ROLE);
    }
    
    /**
     * @notice Special gate for auditors (AUDITOR_ROLE)
     */
    function checkAuditorAccess(address _user) external view returns (bool) {
        if (senatus.isSenator(_user)) return true;
        if (senatus.isDelegate(_user)) return true;
        return senatus.checkRole(_user, AUDITOR_ROLE);
    }
    
    // ========== Dispute Resolution ==========
    
    /**
     * @notice Initiate dispute for refund (handled by Senatus)
     * @param _resourceId Resource identifier
     * @param _reason Dispute reason
     */
    function initiateDisputeForResource(
        bytes32 _resourceId,
        string memory _reason
    ) external {
        Dispute storage dispute = disputes[_resourceId];
        require(!dispute.resolved, "Dispute already resolved");
        
        dispute.disputer = msg.sender;
        dispute.reason = _reason;
        dispute.initiatedAt = block.timestamp;
        dispute.resolved = false;
    }
    
    /**
     * @notice Resolve dispute (Senatus authority)
     * @param _resourceId Resource identifier
     * @param _approve Whether to approve refund
     */
    function resolveDispute(bytes32 _resourceId, bool _approve) external {
        require(senatus.isSenator(msg.sender), "Senators only");
        
        Dispute storage dispute = disputes[_resourceId];
        require(!dispute.resolved, "Already resolved");
        
        dispute.resolved = true;
        dispute.refundApproved = _approve;
        
        emit DisputeResolved(_resourceId, _approve);
    }
    
    // ========== View Functions ==========
    
    function isResourceActive(bytes32 _resource) external view returns (bool) {
        return resourceActive[_resource];
    }
    
    function getResourceRole(bytes32 _resource) external view returns (bytes32) {
        return resourceRoles[_resource];
    }
    
    function getResourceFee(bytes32 _resource) external view returns (uint256) {
        return resourceFees[_resource];
    }
    
    function getUserAccessStatus(
        address _user,
        bytes32 _resource
    ) external view returns (bool hasAccess, uint256 expiresAt) {
        hasAccess = userAccess[_user][_resource] &&
                    block.timestamp <= accessExpiresAt[_user][_resource];
        expiresAt = accessExpiresAt[_user][_resource];
    }
    
    // ========== Internal Helpers ==========
    
    function _uint2str(uint256 value) internal pure returns (string memory) {
        if (value == 0) return "0";
        uint256 temp = value;
        uint256 digits;
        while (temp != 0) {
            digits++;
            temp /= 10;
        }
        bytes memory str = new bytes(digits);
        while (value != 0) {
            digits -= 1;
            str[digits] = bytes1(uint8(48 + uint256(value % 10)));
            value /= 10;
        }
        return string(str);
    }
    
    function _addr2str(address value) internal pure returns (string memory) {
        bytes memory symbols = "0123456789abcdef";
        bytes memory str = new bytes(42);
        str[0] = '0';
        str[1] = 'x';
        for (uint256 i = 0; i < 20; i++) {
            uint8 value_ = uint8(value[i]);
            str[2 + i * 2] = symbols[value_ >> 4];
            str[3 + i * 2] = symbols[value_ & 0x0f];
        }
        return string(str);
    }
}
