// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/**
 * @title PaymentRail
 * @notice RFC 9110 HTTP 402 Payment Required entry point for PYTHAI x402 rails
 * @author Gregory L. (codephreak)
 * @dev No upgradeable proxies. Mainnet only. Immutable post-deploy.
 */

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

contract PaymentRail is Ownable, ReentrancyGuard {
    
    // ========== Type Definitions ==========
    
    enum PaymentState { PENDING, SETTLED, DISPUTED, REFUNDED, EXPIRED }
    
    struct PaymentRecord {
        address payer;
        address payee;
        uint256 amount;
        address token;              // Address(0) for native ETH
        uint256 timestamp;
        uint256 expiresAt;
        bytes32 contentHash;        // SHA-256 of gated content
        uint8 targetChain;          // 1=Eth, 16661=0G Aristotle, etc.
        PaymentState state;
        bytes32 receiptId;
        bool settled;
    }
    
    struct RateLimit {
        uint256 tokensPerSecond;    // Token-bucket rate
        uint256 bucketCapacity;     // Max tokens in bucket
        uint256 lastRefillTime;
        uint256 tokensRemaining;
    }
    
    struct DebasementConfig {
        uint256 launchTime;         // Deployment timestamp
        uint256 dailyDeflateFactor; // 10^12 (0.0001% per day)
        uint256 minDeflateFactor;   // Floor at 50% fee (10^17)
    }
    
    // ========== Storage ==========
    
    mapping(bytes32 => PaymentRecord) public payments;
    mapping(address => RateLimit) public rateLimits;
    mapping(address => bool) public approvedPayees;
    mapping(address => bool) public approvedTokens;
    mapping(address => uint256) public userBalance;
    
    address public validatorRegistry;
    address public senatusDelegation;
    address public treasuryAddress;
    
    DebasementConfig public debasement;
    
    uint256 public constant PAYMENT_EXPIRY = 3600;      // 1 hour
    uint256 public constant MIN_PAYMENT = 10**15;       // 0.001 ETH or equiv
    uint256 public constant VALIDATOR_REWARD_PCT = 30;  // 30% of fees
    
    uint256 private nonce = 0;
    
    // ========== Events ==========
    
    event PaymentInitiated(
        bytes32 indexed receiptId,
        address indexed payer,
        address indexed payee,
        uint256 amount,
        address token,
        bytes32 contentHash
    );
    
    event PaymentSettled(
        bytes32 indexed receiptId,
        uint256 amount,
        address indexed token
    );
    
    event PaymentRefunded(
        bytes32 indexed receiptId,
        uint256 refundAmount,
        string reason
    );
    
    event RateLimitUpdated(
        address indexed user,
        uint256 tokensPerSecond,
        uint256 bucketCapacity
    );
    
    event FeeDistributed(
        address indexed validator,
        uint256 validatorReward,
        address indexed treasury,
        uint256 treasuryReward
    );
    
    event AccessTokenGenerated(
        bytes32 indexed receiptId,
        address indexed user,
        uint256 expiresAt
    );
    
    event DisputeInitiated(
        bytes32 indexed receiptId,
        address indexed disputer,
        string reason
    );
    
    // ========== Constructor ==========
    
    constructor(
        address _validatorRegistry,
        address _senatusDelegation,
        address _treasury
    ) {
        require(_validatorRegistry != address(0), "Invalid registry");
        require(_senatusDelegation != address(0), "Invalid senatus");
        require(_treasury != address(0), "Invalid treasury");
        
        validatorRegistry = _validatorRegistry;
        senatusDelegation = _senatusDelegation;
        treasuryAddress = _treasury;
        
        // Debasement config: launch now, deflate 0.0001% per day
        debasement.launchTime = block.timestamp;
        debasement.dailyDeflateFactor = 10**12;
        debasement.minDeflateFactor = 10**17;  // 50% floor
    }
    
    // ========== Payment Processing ==========
    
    /**
     * @notice Process x402 payment for gated content access
     * @param _contentHash SHA-256 hash of gated content
     * @param _amount Payment amount in smallest unit (wei/microAlgo)
     * @param _token Token address (address(0) for ETH)
     * @param _payee Recipient address (agent/service)
     * @return receiptId Unique payment receipt identifier
     */
    function processPayment(
        bytes32 _contentHash,
        uint256 _amount,
        address _token,
        address _payee
    ) external payable nonReentrant returns (bytes32) {
        require(_amount >= MIN_PAYMENT, "Payment below minimum");
        require(_payee != address(0), "Invalid payee");
        require(approvedPayees[_payee], "Payee not approved");
        
        // Validate token (address(0) = native ETH)
        if (_token != address(0)) {
            require(approvedTokens[_token], "Token not approved");
            require(msg.value == 0, "Do not send ETH with token transfer");
        } else {
            require(msg.value == _amount, "ETH amount mismatch");
        }
        
        // Generate receipt
        bytes32 receiptId = keccak256(
            abi.encodePacked(msg.sender, _amount, block.timestamp, nonce++)
        );
        
        // Record payment
        PaymentRecord storage payment = payments[receiptId];
        payment.payer = msg.sender;
        payment.payee = _payee;
        payment.amount = _amount;
        payment.token = _token;
        payment.timestamp = block.timestamp;
        payment.expiresAt = block.timestamp + PAYMENT_EXPIRY;
        payment.contentHash = _contentHash;
        payment.targetChain = 1;  // Default to Ethereum
        payment.state = PaymentState.PENDING;
        payment.receiptId = receiptId;
        
        // If ERC20: transfer from payer to contract
        if (_token != address(0)) {
            bool success = IERC20(_token).transferFrom(
                msg.sender,
                address(this),
                _amount
            );
            require(success, "Token transfer failed");
        }
        
        emit PaymentInitiated(
            receiptId,
            msg.sender,
            _payee,
            _amount,
            _token,
            _contentHash
        );
        
        return receiptId;
    }
    
    /**
     * @notice Settle payment after confirmation by validators
     * @param _receiptId Payment receipt identifier
     */
    function settlePayment(bytes32 _receiptId) external nonReentrant {
        PaymentRecord storage payment = payments[_receiptId];
        require(payment.payer != address(0), "Payment not found");
        require(payment.state == PaymentState.PENDING, "Invalid state");
        require(block.timestamp <= payment.expiresAt, "Payment expired");
        
        // Calculate debasement fee
        uint256 feeAmount = _calculateDebasementFee(payment.amount);
        uint256 validatorReward = (feeAmount * VALIDATOR_REWARD_PCT) / 100;
        uint256 treasuryReward = feeAmount - validatorReward;
        
        // Mark as settled
        payment.state = PaymentState.SETTLED;
        payment.settled = true;
        
        // Distribute fees
        if (payment.token == address(0)) {
            // Native ETH
            (bool success, ) = treasuryAddress.call{value: treasuryReward}("");
            require(success, "Treasury transfer failed");
        } else {
            // ERC20
            IERC20(payment.token).transfer(treasuryAddress, treasuryReward);
        }
        
        emit PaymentSettled(_receiptId, payment.amount, payment.token);
        emit FeeDistributed(
            msg.sender,
            validatorReward,
            treasuryAddress,
            treasuryReward
        );
    }
    
    /**
     * @notice Generate access token for settled payment
     * @param _receiptId Payment receipt identifier
     * @param _durationSeconds Token validity duration
     * @return accessToken JWT-like token string (client-validated)
     */
    function getAccessToken(
        bytes32 _receiptId,
        uint256 _durationSeconds
    ) external view returns (string memory) {
        PaymentRecord storage payment = payments[_receiptId];
        require(payment.payer == msg.sender, "Not payment owner");
        require(payment.state == PaymentState.SETTLED, "Payment not settled");
        
        uint256 expiresAt = block.timestamp + _durationSeconds;
        
        // Simple token format: receipt.timestamp.expiresAt
        string memory token = string(
            abi.encodePacked(
                _toString(_receiptId),
                ".",
                _toString(payment.timestamp),
                ".",
                _toString(expiresAt)
            )
        );
        
        emit AccessTokenGenerated(_receiptId, msg.sender, expiresAt);
        
        return token;
    }
    
    /**
     * @notice Verify payment validity
     * @param _receiptId Payment receipt identifier
     * @return valid True if payment is settled and not expired
     */
    function verifyPayment(bytes32 _receiptId)
        external
        view
        returns (bool)
    {
        PaymentRecord storage payment = payments[_receiptId];
        return payment.state == PaymentState.SETTLED &&
               block.timestamp <= payment.expiresAt;
    }
    
    /**
     * @notice Refund expired payment
     * @param _receiptId Payment receipt identifier
     * @return refundAmount Amount refunded to payer
     */
    function refundExpired(bytes32 _receiptId)
        external
        nonReentrant
        returns (uint256)
    {
        PaymentRecord storage payment = payments[_receiptId];
        require(payment.payer != address(0), "Payment not found");
        require(
            block.timestamp > payment.expiresAt,
            "Payment not yet expired"
        );
        require(
            payment.state == PaymentState.PENDING ||
            payment.state == PaymentState.DISPUTED,
            "Payment cannot be refunded"
        );
        
        uint256 refundAmount = payment.amount;
        payment.state = PaymentState.REFUNDED;
        
        // Refund to payer
        if (payment.token == address(0)) {
            (bool success, ) = payment.payer.call{value: refundAmount}("");
            require(success, "Refund failed");
        } else {
            IERC20(payment.token).transfer(payment.payer, refundAmount);
        }
        
        emit PaymentRefunded(_receiptId, refundAmount, "Expired");
        
        return refundAmount;
    }
    
    /**
     * @notice Initiate payment dispute (contested by payer)
     * @param _receiptId Payment receipt identifier
     * @param _reason Dispute reason
     */
    function initiateDispute(bytes32 _receiptId, string memory _reason)
        external
    {
        PaymentRecord storage payment = payments[_receiptId];
        require(payment.payer == msg.sender, "Only payer can dispute");
        require(
            payment.state == PaymentState.PENDING ||
            payment.state == PaymentState.SETTLED,
            "Cannot dispute in current state"
        );
        
        payment.state = PaymentState.DISPUTED;
        
        emit DisputeInitiated(_receiptId, msg.sender, _reason);
        
        // Note: Dispute resolution requires Senatus intervention
        // via separate governance mechanism
    }
    
    // ========== Rate Limiting ==========
    
    /**
     * @notice Set rate limit for user (token-bucket algorithm)
     * @param _user User address
     * @param _tokensPerSecond Tokens added per second
     * @param _bucketCapacity Maximum bucket size
     */
    function setRateLimit(
        address _user,
        uint256 _tokensPerSecond,
        uint256 _bucketCapacity
    ) external {
        require(msg.sender == senatusDelegation, "Senatus only");
        
        RateLimit storage limit = rateLimits[_user];
        limit.tokensPerSecond = _tokensPerSecond;
        limit.bucketCapacity = _bucketCapacity;
        limit.lastRefillTime = block.timestamp;
        limit.tokensRemaining = _bucketCapacity;
        
        emit RateLimitUpdated(_user, _tokensPerSecond, _bucketCapacity);
    }
    
    /**
     * @notice Check and consume from rate limit bucket
     * @param _user User address
     * @return remaining Tokens remaining in bucket
     */
    function checkRateLimit(address _user)
        external
        returns (uint256)
    {
        RateLimit storage limit = rateLimits[_user];
        
        if (limit.tokensPerSecond == 0) {
            return 0;  // No limit set
        }
        
        // Refill bucket based on time elapsed
        uint256 timeElapsed = block.timestamp - limit.lastRefillTime;
        uint256 tokensToAdd = (timeElapsed * limit.tokensPerSecond) / 1;
        
        limit.tokensRemaining = _min(
            limit.bucketCapacity,
            limit.tokensRemaining + tokensToAdd
        );
        limit.lastRefillTime = block.timestamp;
        
        if (limit.tokensRemaining >= 1) {
            limit.tokensRemaining -= 1;
            return limit.tokensRemaining;
        }
        
        revert("Rate limit exceeded");
    }
    
    // ========== Admin Functions ==========
    
    function approvePayee(address _payee) external onlyOwner {
        approvedPayees[_payee] = true;
    }
    
    function revokePayee(address _payee) external onlyOwner {
        approvedPayees[_payee] = false;
    }
    
    function approveToken(address _token) external onlyOwner {
        approvedTokens[_token] = true;
    }
    
    function revokeToken(address _token) external onlyOwner {
        approvedTokens[_token] = false;
    }
    
    // ========== Internal Functions ==========
    
    /**
     * @notice Calculate debasement-based fee
     * @dev Fee = (amount * deflate_factor) / 10^18
     *      Deflate factor starts at 10^18, decreases 10^12 per day
     *      Floors at 10^17 (50% fee)
     */
    function _calculateDebasementFee(uint256 _amount)
        internal
        view
        returns (uint256)
    {
        uint256 daysSinceLaunch = (block.timestamp - debasement.launchTime) /
            1 days;
        
        uint256 deflateFactor = 10**18 - (daysSinceLaunch * debasement.dailyDeflateFactor);
        deflateFactor = _max(deflateFactor, debasement.minDeflateFactor);
        
        return (_amount * deflateFactor) / 10**18;
    }
    
    function _min(uint256 a, uint256 b) internal pure returns (uint256) {
        return a < b ? a : b;
    }
    
    function _max(uint256 a, uint256 b) internal pure returns (uint256) {
        return a > b ? a : b;
    }
    
    function _toString(bytes32 value) internal pure returns (string memory) {
        bytes memory symbols = "0123456789abcdef";
        bytes memory str = new bytes(64);
        for (uint256 i = 0; i < 32; i++) {
            uint8 value_ = uint8(value[i]);
            str[i * 2] = symbols[value_ >> 4];
            str[1 + i * 2] = symbols[value_ & 0x0f];
        }
        return string(str);
    }
    
    function _toString(uint256 value) internal pure returns (string memory) {
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
    
    // ========== Fallback ==========
    
    receive() external payable {
        // Allow ETH deposits
    }
}
