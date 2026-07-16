# PYTHAI x402 Payment Rails: Quick Start Guide

**5-minute setup** for testing x402 payment flow on testnet, or skip to **Production Deployment** section for mainnet.

---

## Option A: Local Testing (Hardhat)

### 1. Clone & Install

```bash
git clone <pythai-x402-rails>
cd pythai-x402-rails/foundry
npm install
forge install
```

### 2. Compile

```bash
forge build
```

### 3. Run Tests

```bash
forge test --gas-limit 30000000 --fuzz-runs 100
```

**Expected output:**
```
[PASS] PaymentRailTest::testProcessPaymentETH
[PASS] PaymentRailTest::testRateLimitBucketRefill
[PASS] PaymentRailTest::testDebasementFeeCalculation
... (100+ tests)
```

### 4. Deploy to Local Fork

```bash
# Start fork (requires Alchemy/Infura key)
anvil --fork-url https://eth-mainnet.g.alchemy.com/v2/$ALCHEMY_KEY

# In another terminal: deploy
forge script script/Deploy.s.sol:PaymentRailScript \
  --rpc-url http://127.0.0.1:8545 \
  --broadcast \
  --private-key 0x...
```

---

## Option B: Ethereum Sepolia Testnet

### 1. Environment Setup

```bash
# Create .env file
cat > .env << EOF
SEPOLIA_RPC_URL=https://sepolia.infura.io/v3/YOUR_INFURA_KEY
SEPOLIA_PRIVATE_KEY=0x...
ETHERSCAN_API_KEY=YOUR_ETHERSCAN_KEY
EOF

# Load
source .env
```

### 2. Deploy to Sepolia

```bash
forge script script/Deploy.s.sol:PaymentRailScript \
  --rpc-url $SEPOLIA_RPC_URL \
  --private-key $SEPOLIA_PRIVATE_KEY \
  --broadcast \
  --verify \
  --etherscan-api-key $ETHERSCAN_API_KEY
```

### 3. Test Payment Flow

```python
from web3 import Web3
from eth_account import Account

# Connect
w3 = Web3(Web3.HTTPProvider("https://sepolia.infura.io/v3/YOUR_KEY"))

# Create payer account
payer = Account.from_key("0x...")
payee = "0x..."

# Build transaction
payment_tx = {
    "to": "0x<PaymentRail>",
    "from": payer.address,
    "value": w3.to_wei(1, "ether"),
    "gas": 100000,
    "gasPrice": w3.eth.gas_price,
    "nonce": w3.eth.get_transaction_count(payer.address),
    "data": "0x...",  # Encoded contract call
}

# Sign & send
signed = payer.sign_transaction(payment_tx)
tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)

# Wait
receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
print(f"Payment settled: {receipt['blockNumber']}")
```

---

## Option C: Algorand Testnet

### 1. Get Test Algo

Visit: https://dispenser.testnet.fi.algorand.network/

### 2. Deploy BONAFIDE

```bash
# Set credentials
export ALGONODE_TOKEN="YOUR_TOKEN"
export DEPLOYER_MNEMONIC="25-word seed phrase"

# Deploy
cd algorand
python3 bonafide_deploy.py

# Output
# ✓ Genius deployed: App ID 1234567
# ✓ BonaToken deployed: App ID 1234568
# ...
```

### 3. Create x402-Gated Resource

```python
from algosdk import mnemonic
from pyteal import *

# Generate keypair
mnemonic_key = "YOUR 25-WORD MNEMONIC"
sk = mnemonic.to_private_key(mnemonic_key)
addr = mnemonic.to_public_key(mnemonic_key)

# Deploy x402 gating app
approval_program = compileTeal(
    Seq([
        # x402 gating logic
        Return(Int(1))
    ]),
    Mode.Application
)

# Submit to testnet
```

---

## Option D: Minimal HTTP Client Test

### Without Deployment (Mock Server)

```python
import requests
import json
from time import time

# Mock API endpoint
BASE_URL = "http://localhost:8000"

# Step 1: Attempt unauthorized request
response = requests.get(
    f"{BASE_URL}/api/agents/agent-1/infer",
    json={"query": "test"}
)

assert response.status_code == 402  # Payment Required

# Step 2: Simulate payment
payment_response = requests.post(
    f"{BASE_URL}/api/payments/process",
    json={
        "payer": "0x1234...",
        "amount": 1000000,  # 1 USDC
        "token": "USDC",
        "content_hash": "0xdeadbeef..."
    }
)

receipt_id = payment_response.json()["receipt_id"]

# Step 3: Verify payment
verification = requests.get(
    f"{BASE_URL}/api/payments/{receipt_id}/verify"
)

assert verification.json()["settled"] == True

# Step 4: Authorized request with proof
headers = {
    "Authorization": f"CAIP-122 0x1234...:{signature}",
    "X-Payment-Receipt": receipt_id
}

result = requests.post(
    f"{BASE_URL}/api/agents/agent-1/infer",
    json={"query": "test"},
    headers=headers
)

assert result.status_code == 200
print(result.json())
```

---

## Production Deployment Checklist

| Step | Command | Status |
|------|---------|--------|
| 1. Compile contracts | `forge build --optimizer-runs 100000` | ✓ |
| 2. Run full test suite | `forge test --gas-limit 30000000` | ✓ |
| 3. Deploy ValidatorRegistry | `forge script Deploy.s.sol:ValidatorRegistry` | ✓ |
| 4. Deploy PaymentRail | `forge script Deploy.s.sol:PaymentRail` | ✓ |
| 5. Deploy X402Gate | `forge script Deploy.s.sol:X402Gate` | ✓ |
| 6. Deploy BONAFIDE (Algo) | `python3 bonafide_deploy.py` | ✓ |
| 7. Link governance (Senatus) | `cast send ... transferOwnership(...)` | ✓ |
| 8. Initialize rate limits | REST API call | ✓ |
| 9. Enable agent discovery | AgenticPlace registration | ✓ |
| 10. Health check all chains | `./deployment/scripts/health_check.sh` | ✓ |

---

## Common Integration Patterns

### Pattern 1: Ethereum → Agent Payment

```solidity
// SmartContract → PaymentRail
function payForAgentAccess(
    address paymentRail,
    address agent,
    uint256 amount
) external payable {
    (bool success, ) = paymentRail.call{value: amount}(
        abi.encodeWithSignature(
            "processPayment(bytes32,uint256,address,address)",
            keccak256(abi.encodePacked(agent)),
            amount,
            address(0),  // ETH
            agent
        )
    );
    require(success, "Payment failed");
}
```

### Pattern 2: Algorand → Ethereum Bridge

```python
from algosdk.transaction import PaymentTxn

# User pays in Algo
txn = PaymentTxn(
    sender=user_addr,
    sp=params,
    receiver=payment_rail_app_addr,
    amt=1000000  # 1 Algo
)

# Bridge monitors Algorand block
# Auto-triggers Ethereum L2MinterBurner
# mints equivalent xERC20 on destination
```

### Pattern 3: Multi-Agent Rate Limiting

```python
from pythai_rails import X402PaymentBridge

bridge = X402PaymentBridge(...)

# Initialize per-agent rate limits
await bridge.set_rate_limit(
    user_address="0x...",
    tokens_per_second=10.0,   # Shared across all agents
    bucket_capacity=1000.0
)

# Check per-agent quota
agent_1_allowed, remaining = bridge.check_rate_limit("0x...", tokens=1)
agent_2_allowed, remaining = bridge.check_rate_limit("0x...", tokens=1)
```

---

## Troubleshooting

### "Payment verification failed"

**Cause**: Receipt ID not found on-chain.

**Fix**:
```bash
# Verify transaction on explorer
cast call 0x<PaymentRail> \
  "payments(bytes32)" \
  0x<receipt_id>

# If empty, payment didn't settle
# Wait for validators' consensus confirmation
```

### "Rate limit exceeded"

**Cause**: Token bucket empty.

**Fix**:
```bash
# Check rate limit state
cast call 0x<PaymentRail> \
  "rateLimits(address)" \
  0x<user_addr>

# Reset (Senatus admin only)
cast send 0x<PaymentRail> \
  "setRateLimit(address,uint256,uint256)" \
  0x<user_addr> 10000 100000
```

### "HTTP 402 Payment Required"

**Expected behavior**. To resolve:

1. Generate CAIP-122 signature
2. Call `/api/payments/process` to settle
3. Wait for validator consensus
4. Retry with `X-Payment-Receipt` header

---

## Next Steps

1. **Read ARCHITECTURE.md** for technical deep dive
2. **Review DEPLOYMENT.md** for mainnet rollout plan
3. **Check LIMITATIONS.md** for known constraints
4. **Integrate with mindX** via `integrations/mindx/bdi_payment_bridge.py`
5. **Monitor on Blockscout** (Ethereum) or Algoexplorer (Algorand)

---

## Support

- **Docs**: rage.pythai.net
- **API**: agenticplace.pythai.net/api
- **Governance**: senatus.pythai.net
- **Emergency**: codephreak@pythai.net

---

*PYTHAI x402 Payment Rails v1.0.0*
*No upgradeable proxies. Mainnet only. Immutable post-deploy.*
