# PYTHAI x402 Payment Rails: Technical Architecture

## Overview

The x402 payment rails implement RFC 9110 Payment Required semantics across a sovereign, multi-chain infrastructure. This document specifies the technical architecture, state machines, and integration contracts.

---

## 1. Core Components

### 1.1 Payment Rail (PaymentRail.sol)

**Purpose**: Entry point for all x402 payment processing, xERC20 minting/burning, and cross-chain settlement.

**State**:
```solidity
struct PaymentRecord {
    address payer;
    address payee;
    uint256 amount;
    address token;
    uint256 timestamp;
    bytes32 contentHash;
    uint8 chainId;
    bool settled;
}

struct RateLimit {
    uint256 tokensPerSecond;
    uint256 bucketCapacity;
    uint256 lastRefillTime;
    uint256 tokensRemaining;
}
```

**Key Functions**:
- `processPayment(bytes32 contentHash, uint256 amount, address token)` → `bytes32 receiptId`
- `getAccessToken(address user, uint256 expirySeconds)` → `string x402Token`
- `verifyPayment(bytes32 receiptId)` → `bool valid`
- `refundExpired(bytes32 receiptId)` → `uint256 refunded`

**Fee Model**: DebasementIndexV2
- Base fee = `(amount * deflateFactor) / 10^18`
- Deflate factor = `10^18 - (daysSinceLaunch * 10^12)` (caps at 50% over 1 year)
- Validator reward = `fee * 30%`
- Treasury = `fee * 70%`

### 1.2 x402 Access Gate (X402AccessGate.sol)

**Purpose**: Enforce payment requirements via standard x402 HTTP semantics, linked to DAIO governance.

**Integration Points**:
- Senatus role validation (senator, auditor, deployer)
- DAIO multisig access control
- ChainRegistry endorsement
- Deflationary fee scheduling

**State Machine**:
```
[No Payment] --processPayment--> [Pending] --settle--> [Settled] --expiry--> [Expired]
                                     |
                                     +--dispute--> [Disputed] --resolve--> [Settled/Refund]
```

**x402 Headers**:
```
HTTP/1.1 402 Payment Required
WWW-Authenticate: x402; realm="pythai-payment"; token="..."
Retry-After: 3600
X-PYTHAI-Rate-Limit: 1000
X-PYTHAI-Fee-Model: debasement-v2
X-PYTHAI-Receipt-Id: 0x...
```

### 1.3 ERC-8004 Identity Registry (EAIO.sol)

**Live Contract**: `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` (Ethereum Mainnet)

**Purpose**: Persistent, cryptographically verifiable identity registry for agents and payment streams.

**State**:
```solidity
struct Identity {
    bytes32 did;                   // Decentralized Identifier
    address ethAddress;            // EVM binding
    string algoAddress;            // Algorand binding
    bytes32 publicKeyHash;         // Falcon-1024 (post-quantum)
    uint256 registeredAt;
    uint256 expiresAt;
    bool active;
}

struct Agent {
    bytes32 identityDid;
    string name;
    string apiUrl;
    uint256 createdAt;
    bool acceptsPayments;
}
```

**Events**:
```solidity
event IdentityRegistered(bytes32 indexed did, address ethAddr, string algoAddr);
event AgentOnboarded(bytes32 indexed agentId, bytes32 identityDid);
event PaymentTermsUpdated(bytes32 indexed identityDid, uint256 rateLimit, address token);
```

### 1.4 Bridge Components (openBDK)

#### L1Escrow.sol (Ethereum Mainnet)
- Locks xERC20 tokens for cross-chain transfer
- Validates 1R+3V BFT consensus from validators
- Emits `TokensLocked(bytes32 txHash, uint256 amount, address token, string targetChain)`

#### L2MinterBurner.sol (Target Chain, e.g., 0G Aristotle)
- Mints equivalent xERC20 tokens on destination
- Burns tokens on return transfer
- Maintains 1:1 peg via oracle-less validator consensus

#### ValidatorRegistry.sol (All Chains)
- **Topology**: 1 Required + 3 Optional (1R+3V)
- **Consensus**: Required validator + majority of optional validators
- **Rewards**: 30% of bridge fees to validator set
- **Slashing**: If validator signs conflicting transfer, 10% stake slashed to treasury

---

## 2. Payment Flow

### 2.1 Request Flow (Client → Service)

```
1. Client initiates request to x402-gated resource
   GET /api/agent-inference
   Authorization: CAIP-122 <address>:<signature>

2. Server checks x402 state:
   - If access token valid AND rate limit OK → proceed (200)
   - If rate limit exceeded → return (402) with x402 headers
   - If token invalid → return (401) with wallet redirect

3. Client receives (402) Payment Required:
   HTTP/1.1 402 Payment Required
   WWW-Authenticate: x402; realm="pythai"; token="eyJ..."
   X-PYTHAI-Rate-Limit: 1000
   X-PYTHAI-Fee: 0.001 PYTHAI
   X-PYTHAI-Receipt-Id: 0x...

4. Client constructs payment:
   - Amount: 0.001 PYTHAI
   - Destination: PaymentRail contract
   - Metadata: receiptId, contentHash, targetResource

5. Client executes payment (Algorand or Ethereum):
   Algorand: axfer from user -> PaymentRail.appId, amount 1000 microAlgo
   Ethereum: transfer from user -> PaymentRail contract, 10^15 wei

6. Server monitors payment confirmation:
   - Algorand: 4+ confirmations (~12 seconds)
   - Ethereum: 12+ confirmations (~3 minutes)

7. Server grants access token:
   HTTP/1.1 200 OK
   X-PYTHAI-Access-Token: <JWT with proof-of-payment>
   X-PYTHAI-Expires-In: 3600

8. Client proceeds with actual request using access token.
```

### 2.2 Rate Limiting (Token Bucket)

```python
def check_rate_limit(user_id: str, rate_limit: RateLimit) -> bool:
    now = time.time()
    time_elapsed = now - rate_limit.last_refill_time
    tokens_to_add = time_elapsed * rate_limit.tokens_per_second
    
    rate_limit.tokens_remaining = min(
        rate_limit.bucket_capacity,
        rate_limit.tokens_remaining + tokens_to_add
    )
    rate_limit.last_refill_time = now
    
    if rate_limit.tokens_remaining >= 1.0:
        rate_limit.tokens_remaining -= 1.0
        return True
    
    return False
```

### 2.3 Settlement (Validator Consensus)

**For xERC20 Bridge Transfer**:
```
1. User calls PaymentRail.bridgeTokens(amount, targetChain, targetAddress)
   → Emits LockEvent with transfer hash

2. All validators (1R+3V) observe LockEvent
   
3. Required validator (1R) signs immediately:
   signature = sign(keccak256(transferHash), privateKey)
   
4. Optional validators (3V) sign if conditions met:
   - Transfer follows confirmed payment on source chain
   - Destination has sufficient liquidity
   - No conflicting transfer for same amount/nonce
   
5. Consensus reached when:
   1R signature present AND (2 of 3V signatures present OR 60 seconds elapsed)
   
6. MinterBurner on destination chain receives consensus proof:
   - Validates all 1R+remaining 3V signatures
   - Checks lockEvent timestamp < 10 minutes (anti-replay)
   - Mints xERC20 tokens to targetAddress
   - Emits MintEvent
   
7. If dispute detected (conflicting signatures):
   - ValidatorRegistry.slash(validator, reason)
   - 10% of validator stake transferred to Treasury
```

---

## 3. DAIO Governance Integration

### 3.1 Senate (Senatus) - Legislative Authority

```solidity
contract Senatus {
    mapping(bytes32 => Proposal) proposals;
    mapping(bytes32 => mapping(address => bool)) votes;
    
    struct Proposal {
        string title;
        bytes calldata;
        uint256 submitBlock;
        uint256 votingDeadline;
        uint256 votesFor;
        uint256 votesAgainst;
        bool executed;
    }
    
    // Supermajority requirement: 66.6% of senators
    function vote(bytes32 proposalId, bool approval) {
        require(isSenator[msg.sender]);
        votes[proposalId][msg.sender] = approval;
    }
    
    function execute(bytes32 proposalId) {
        Proposal p = proposals[proposalId];
        require(p.votesFor > (totalVotes * 666) / 1000); // 66.6%
        p.calldata.call();
        p.executed = true;
    }
}
```

### 3.2 Access Gate (X402AccessGate)

**Role-Based Permissions**:
```solidity
enum Role { NONE, AUDITOR, DEPLOYER, AGENT_OPERATOR, SENTINEL }

mapping(bytes32 => mapping(Role => bool)) roleGates;

function checkAccess(
    bytes32 identityDid,
    Resource resource,
    Role requiredRole
) public view returns (bool) {
    return (
        roleGates[identityDid][requiredRole] ||
        senatus.isSenatusDelegate(identityDid)
    );
}
```

### 3.3 War Council Integration

**Resource Allocation Verdicts**:
- `WAGE`: Increase x402 fee/unlock token supply
- `SUBDUE`: Enforce rate-limiting or slashing
- `HOLD`: Maintain status quo
- `WITHDRAW`: Freeze payment rail or revoke access

---

## 4. Algorand Constitutional Layer (BONAFIDE)

### 4.1 Contract Suite (Algopy)

```
Genius (App ID: TBD)
  └─ Core governance logic
  └─ Token issuance authorization
  
BonaToken (App ID: TBD)
  └─ x402-gated USDC bridges
  └─ Fee distribution
  
Tabularium (App ID: TBD)
  └─ Record-keeping
  └─ History immutability
  
Fides (App ID: TBD)
  └─ Reputation tracking
  └─ Trust scores
  
SponsioPactum (App ID: TBD)
  └─ Sponsorship & patronage
  
Censura (App ID: TBD)
  └─ Audit logs
  └─ Compliance checks
  
Senatus (App ID: TBD)
  └─ On-chain voting
  
Tessera (App ID: TBD)
  └─ Multi-sig escrow
  
Aerarium (App ID: TBD)
  └─ Treasury management
```

### 4.2 x402 Gating on Algorand

**PARSEC/GoPlausible x402-avm Runtime**:
- Runs on Algorand AVM v12+
- Content-addressed storage via SHA-256 hashing
- Token-bucket throttle with POSIX 12-bit permissions
- DebasementIndexV2 fee calculation

**Example ASA Lock**:
```
ASA ID: 31566704 (USDC)
Lockup: 10 USDC minimum for x402 token valid for 3600 seconds
Payment: User opts-in to x402 gating app
         App stores token-bucket state in local state
         User transfers USDC to Tabularium escrow
         Tabularium releases x402 token
```

---

## 5. Multi-Chain ChainMapping (AgenticPlace)

### 5.1 Chain Registry (Foundry)

**Live Registry**: agenticplace.pythai.net/allchain.html

**Contract Interface**:
```solidity
interface IChainRegistry {
    struct ChainEntry {
        uint256 chainId;
        string name;
        string rpcUrl;
        address erc8004Registry;
        address paymentRail;
        bool isActive;
        uint256 lastHeartbeat;
    }
    
    function registerChain(ChainEntry entry) external onlySenatusDelegate;
    function getChainById(uint256 chainId) external view returns (ChainEntry);
    function getAllChains() external view returns (ChainEntry[]);
}
```

**Supported Chains** (2,500+ via mindX allchain registry):
```
Mainnet:
  - Ethereum (1)
  - Algorand (caip-2)
  - Solana (solanart:...)
  - Arweave/AO (ar://...)
  
Settlement:
  - 0G Aristotle (16661) [EIP-8141 Frame Tx support]
  - Polygon (137) [Uniswap V3]
  - Arbitrum (42161) [openBDK bridge]
  
Payment Rails:
  - Moonbeam (1284) [SPINTRADE DEX]
  - BSC (56) [THRUST DeltaV]
  - Avax C-Chain (43114)
```

### 5.2 Agent Discovery

**Endpoint**: `GET /api/agents/discover?chain={chainId}&capability={paymentSupport|inference|storage}`

**Response**:
```json
{
  "agents": [
    {
      "did": "did:eth:0x8004...",
      "name": "agent-1",
      "apiUrl": "https://agent-1.local/api",
      "supportedChains": [1, 16661, 137],
      "paymentAccepts": ["PYTHAI", "USDC", "ETH"],
      "rateLimit": 1000,
      "averageLatency": 145,
      "reputation": 0.98
    }
  ],
  "chainMapping": {
    "1": { "registry": "0x8004...", "rail": "0xPaymentRail..." },
    "16661": { "registry": "0x0GReg...", "rail": "0x0GRail..." }
  }
}
```

---

## 6. mindX Integration

### 6.1 BDI Agent with x402 Payment

**Architecture**:
```
┌─────────────────────────────┐
│  mindX BDI Agent            │
│  (Darwin-Gödel Cognitive)   │
├─────────────────────────────┤
│  Beliefs:                   │
│   - User identity (CAIP-122)│
│   - Payment history         │
│   - Rate limit quota        │
├─────────────────────────────┤
│  Desires:                   │
│   - Maximize accuracy       │
│   - Minimize latency        │
│   - Optimize fee collection │
├─────────────────────────────┤
│  Intentions:                │
│   - Gate on x402 payment    │
│   - Validate CAIP-122 sig   │
│   - Update rate limit       │
│   - Process inference       │
└──────────────────┬──────────┘
                   │
         ┌─────────┴─────────┐
         │                   │
    ┌────▼────┐        ┌─────▼────┐
    │ RAGE     │        │ MASTER-  │
    │ Memory   │        │ MIND     │
    │ (local)  │        │ (global) │
    └────┬─────┘        └────┬─────┘
         │                   │
         └─────────┬─────────┘
                   │
         ┌─────────▼──────────┐
         │ x402PaymentBridge  │
         │ (Algorand + EVM)   │
         └────────────────────┘
```

**Request Handler**:
```python
@app.post("/api/agents/{agent_id}/infer")
async def gated_inference(
    agent_id: str,
    input_data: Dict,
    authorization: str = Header(...)  # CAIP-122
):
    # 1. Parse CAIP-122 signature
    caip122 = CAIP122Parser.parse(authorization)
    payer_address = caip122.address
    
    # 2. Check x402 payment status
    payment_status = await x402_bridge.check_payment(
        payer_address, agent_id
    )
    
    if not payment_status.valid:
        return JSONResponse(
            status_code=402,
            content={"error": "payment_required"},
            headers={
                "WWW-Authenticate": "x402; realm='pythai'",
                "X-PYTHAI-Receipt-Id": payment_status.receipt_id
            }
        )
    
    # 3. Update rate limit
    remaining = await rate_limiter.consume(payer_address, 1)
    
    # 4. Execute BDI inference
    result = await agent.infer(input_data, context={
        "payer": payer_address,
        "payment_proof": payment_status.receipt
    })
    
    return {
        "result": result,
        "rate_limit_remaining": remaining
    }
```

### 6.2 RAGE Memory (Local Agent State)

```python
class RAGEMemory:
    """
    Recency-Augmented Guided Experience memory for agents.
    Tracks per-user: recent payments, rate limit quota, inferred state.
    """
    
    def __init__(self):
        self.recent_payments = {}  # address -> [payments]
        self.rate_limits = {}      # address -> RateLimit
        self.user_state = {}       # address -> inferred context
    
    def record_payment(self, payer: str, amount: int, tx_hash: str):
        self.recent_payments[payer].append({
            "amount": amount,
            "tx_hash": tx_hash,
            "timestamp": time.time()
        })
    
    def get_user_context(self, payer: str) -> Dict:
        """Return contextual state for belief/desire/intention update."""
        return {
            "recent_balance": sum(p["amount"] for p in self.recent_payments[payer][-10:]),
            "frequency": len([p for p in self.recent_payments[payer] 
                            if time.time() - p["timestamp"] < 3600]),
            "rate_limit_remaining": self.rate_limits[payer].tokens_remaining
        }
```

---

## 7. Post-Quantum Cryptography (EIP-8141)

### 7.1 Frame Transactions (Type 0x06)

**For 0G Aristotle (chain ID 16661)**:
```solidity
// EIP-8141 Frame Transaction
// type: 0x06
// signature: Falcon-1024 (post-quantum)

struct FrameTransaction {
    uint256 chainId;        // 16661 (0G Aristotle)
    uint256 nonce;
    uint256 maxFeePerGas;
    uint256 maxPriorityFee;
    uint256 gasLimit;
    address to;
    uint256 value;
    bytes data;
    
    // Post-quantum signature
    bytes sig;              // Falcon-1024 (1792 bytes)
    bytes pubKey;           // 1792 bytes
    
    // CFI (Crypto Foundation Identifier) for Hegotá fork
    bytes32 cfi;
}
```

### 7.2 Precompile Support (EIP-8051/8052)

**Precompile Addresses**:
- `0x0b`: ML-DSA verification (EIP-8051)
- `0x0c`: Falcon verification (EIP-8052)

**Usage**:
```solidity
function verifyFrameTransaction(FrameTransaction tx) internal returns (bool) {
    // Call precompile 0x0c for Falcon verification
    (bool success, bytes memory result) = address(0x0c).staticcall(
        abi.encodePacked(tx.data, tx.sig, tx.pubKey)
    );
    return success && (result[0] == 0x01);
}
```

---

## 8. API Gateway (mindx.pythai.net)

### 8.1 OpenAPI 3.1 Spec (api/openapi.yaml)

**350+ Endpoints** covering:
- Payment processing (POST /payments/process)
- Identity resolution (GET /identities/{did})
- Rate limiting (GET /users/{addr}/rate-limit)
- Agent discovery (GET /agents/discover)
- Governance voting (POST /governance/senatus/vote)
- Bridge management (POST /bridge/transfer)
- Content storage (POST /content/store, GET /content/{hash})

### 8.2 Authentication (CAIP-122)

```typescript
// Supports: EVM, Algorand, Solana, Cardano, Bitcoin
function verifyCaip122(
    message: string,
    signature: string,
    chainNamespace: string,  // "eip155" | "algorand" | "solana"
    chainReference: string   // "1" | "mainnet-v1" | ...
): Promise<string> {  // returns address
    
    const caipId = `${chainNamespace}:${chainReference}`;
    
    switch (chainNamespace) {
        case "eip155":
            return ethers.verifyMessage(message, signature);
        case "algorand":
            return algorandSignatureVerify(message, signature);
        case "solana":
            return solanaSignatureVerify(message, signature);
        // ...
    }
}
```

---

## 9. Deployment & Testing Strategy

### 9.1 Foundry Testing

**Test Coverage**:
```
PaymentRail.sol:
  ✓ processPayment() with various token amounts
  ✓ getAccessToken() expires correctly
  ✓ verifyPayment() with valid/invalid receipts
  ✓ refundExpired() claims refund
  ✓ Rate limiting enforces bucket correctly
  
X402AccessGate.sol:
  ✓ HTTP 402 responses for unpaid requests
  ✓ Role-based access via DAIO
  ✓ Fee model (debasement-v2) calculates correctly
  ✓ Dispute resolution
  
Bridge (L1Escrow + L2MinterBurner + ValidatorRegistry):
  ✓ 1R+3V consensus reached in 90% of cases
  ✓ Slashing triggers on conflicting signatures
  ✓ xERC20 peg maintained (1:1 lock/mint)
  ✓ Cross-chain transfers settle in <5 minutes (Ethereum)
  
EAIO.sol:
  ✓ Identity registration immutable
  ✓ Agent onboarding with DID binding
  ✓ Payment terms updates via Senatus
```

**Command**:
```bash
forge test --gas-limit 30000000 --fork-url https://eth.llamarpc.com \
  --fork-block-number 21000000 --fuzz-runs 1000
```

### 9.2 Mainnet Deployment Flow

```
Phase 1: Validator Consensus
  1. Deploy ValidatorRegistry on Ethereum
  2. Register 1 required + 3 optional validators
  3. Distribute signing keys via hardware wallets
  4. Perform consensus dry-run (10 test transfers)

Phase 2: Core Contracts
  1. Deploy PaymentRail (on Ethereum, 0G Aristotle, Algorand)
  2. Deploy X402AccessGate, link to PaymentRail
  3. Deploy EAIO identity registry
  4. Transfer admin keys to Senatus multisig
  5. Renounce deployer key (immutable)

Phase 3: Bridge Activation
  1. Deploy L1Escrow on Ethereum
  2. Deploy L2MinterBurner on each target chain
  3. Configure cross-chain messaging (LayerZero or native)
  4. Perform bridge health check (100 test transfers)

Phase 4: Governance Initialization
  1. Deploy Senatus with initial senator set (7-of-11 multisig)
  2. Deploy Curia (executive proposals)
  3. Deploy Censura (audit log)
  4. Deploy WarCouncil with Sun Tzu 13 chapters
  5. Deploy Boardroom + DeadmansSwitch

Phase 5: Algorand BONAFIDE
  1. Deploy all 9 contracts on Algorand mainnet
  2. Link BonaToken to USDC ASA 31566704
  3. Configure x402-avm gating (via PARSEC)
  4. Initialize fee distribution to ValidatorRegistry

Phase 6: AgenticPlace Integration
  1. Populate ChainRegistry with 2,500+ chains
  2. Update agent discovery endpoints
  3. Launch public beta (rate-limited)

Phase 7: mindX Activation
  1. Deploy API gateway (fastapi + websocket)
  2. Enable 350+ routes
  3. Link to rage.pythai.net publishing
  4. Monitor for 30 days before full load

Phase 8: Post-Quantum Readiness
  1. Generate Falcon-1024 keypairs for 0G Aristotle validators
  2. Deploy EIP-8141 precompiles on 0G (if not yet included)
  3. Enable Frame Transactions for all bridge transfers
  4. Publish CFI (Crypto Foundation Identifier) for Hegotá fork
```

---

## 10. Limitations & Edge Cases

**See LIMITATIONS.md for full treatment. Summary**:
- Max rate limit: 2^32 tokens/second (token-bucket overflow)
- Validator consensus timeout: 60 seconds (auto-settle with 1R + majority 3V)
- Dispute resolution: Up to 7 days (Senatus quorum requirement)
- Post-quantum key rotation: Quarterly (Senatus vote required)
- Cross-chain settlement: Ethereum-bound (3 min), Algorand-instant (4 sec)

---

*This architecture implements cypherpunk2048 standards: no admin keys post-deploy, no upgradeable proxies, mainnet only, clear immutable governance.*
