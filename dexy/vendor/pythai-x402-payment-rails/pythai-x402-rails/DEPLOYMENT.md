# PYTHAI x402 Payment Rails: Mainnet Deployment Guide

## Overview

This guide covers deploying the complete x402 payment rails infrastructure across:
- **Ethereum Mainnet** (Layer 2 anchor, ERC-8004)
- **0G Aristotle** (Chain ID 16661, EIP-8141 Frame Transactions)
- **Algorand Mainnet** (Constitutional layer, BONAFIDE suite)

**Total estimated deployment time**: 4-6 weeks (phased rollout)

---

## Phase 1: Validator Registry & Consensus Setup (Week 1)

### 1.1 Validator Infrastructure

**Validator Set Topology**: 1 Required + 3 Optional (1R+3V)

#### Required Validator (1R)
- **Primary role**: Immediate signature generation
- **Hardware**: 2x Intel Xeon 32-core, 512GB RAM, NVMe SSD
- **Network**: Dedicated 10Gbps circuit
- **Redundancy**: Geographic separation (us-east-1, eu-west-1)

```bash
# Hardware signing setup (no hot key export)
# Using AWS CloudHSM or YubiHSM 2

# Required validator keypair generation
aws cloudsmb-create-key \
  --key-spec ASYMMETRIC_SIGNING \
  --key-usage SIGN_VERIFY \
  --origin AWS_KMS

# Output: arn:aws:kms:...
```

#### Optional Validators (3V)
- **Role**: Consensus support, secondary signing
- **Configuration**: Same spec as 1R, distributed geographically
- **Failover**: Automatic promotion if 1R becomes unavailable

### 1.2 ValidatorRegistry Deployment

**Contract**: `foundry/src/core/ValidatorRegistry.sol`

```bash
# 1. Compile contracts
cd foundry
forge build --optimizer-runs 100000

# 2. Create deployment wallet (multi-sig, recommend Gnosis Safe)
# Use 3-of-5 multisig for registry management

# 3. Deploy ValidatorRegistry
forge script script/Deploy.s.sol:ValidatorRegistryScript \
  --rpc-url https://eth.llamarpc.com \
  --broadcast \
  --verify \
  --etherscan-api-key $ETHERSCAN_API_KEY

# 4. Register validators
forge script script/RegisterValidators.s.sol \
  --rpc-url https://eth.llamarpc.com \
  --broadcast \
  --private-key $MULTISIG_KEY
```

**Output**:
```
✓ ValidatorRegistry deployed at: 0x...
✓ Required validator registered: 0x...
✓ Optional validators registered: 0x..., 0x..., 0x...
✓ Consensus threshold: 1R + 2 of 3V
```

### 1.3 Validator Key Distribution

```bash
# Generate key material for all validators
# CRITICAL: Use air-gapped hardware security modules (HSM)

# 1. Validator 1R (Required)
aws cloudsmb-create-key \
  --alias "pythai-validator-1r" \
  --description "PYTHAI 1R validator signing key"

# 2. Validators 3V (Optional)
for i in {1..3}; do
  aws cloudsmb-create-key \
    --alias "pythai-validator-3v-$i" \
    --description "PYTHAI optional validator $i signing key"
done

# 3. Distribute keys via:
# - Sealed envelopes (for physical backup)
# - Encrypted USB (for digital backup)
# - No cloud storage (air-gapped only)
```

**DO NOT**: Export private keys. Use AWS KMS, YubiHSM, or Ledger Vault.

---

## Phase 2: Core EVM Contracts (Week 2)

### 2.1 PaymentRail Deployment

```bash
# 1. Set environment variables
export VALIDATOR_REGISTRY="0x..."
export SENATUS_DELEGATION="0x..."
export TREASURY_ADDRESS="0x..."

# 2. Deploy PaymentRail
forge script script/Deploy.s.sol:PaymentRailScript \
  --rpc-url https://eth.llamarpc.com \
  --broadcast \
  --verify \
  --etherscan-api-key $ETHERSCAN_API_KEY

# 3. Verify contract
etherscan-verify 0x<PaymentRail> --etherscan-api-key $ETHERSCAN_API_KEY
```

**Output**:
```
✓ PaymentRail deployed at: 0x...
✓ Verified on Etherscan
✓ Constructor params: ValidatorRegistry, Senatus, Treasury
```

### 2.2 X402AccessGate Deployment

```bash
export PAYMENT_RAIL="0x..."
export SENATUS_ADDR="0x..."

forge script script/Deploy.s.sol:X402GateScript \
  --rpc-url https://eth.llamarpc.com \
  --broadcast \
  --verify \
  --etherscan-api-key $ETHERSCAN_API_KEY
```

### 2.3 ERC-8004 Identity Registry

**Note**: ERC-8004 registry should be pre-deployed at:
```
0x8004A169FB4a3325136EB29fA0ceB6D2e539a432 (Ethereum Mainnet)
```

If not present, deploy:

```bash
forge script script/Deploy.s.sol:EAIO Script \
  --rpc-url https://eth.llamarpc.com \
  --broadcast
```

### 2.4 Bridge Components (openBDK)

**L1Escrow** (Ethereum):
```bash
forge script script/Deploy.s.sol:L1EscrowScript \
  --rpc-url https://eth.llamarpc.com \
  --broadcast
```

**L2MinterBurner** (0G Aristotle, chain ID 16661):
```bash
export L1_ESCROW="0x..."

forge script script/Deploy.s.sol:L2MinterBurnerScript \
  --rpc-url https://aristotle-rpc.zerogwei.com \
  --broadcast
```

---

## Phase 3: Algorand Constitutional Layer (Week 3)

### 3.1 BONAFIDE Suite Deployment

**Target**: Algorand Mainnet (network ID `algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=`)

**Requirements**:
- Algopy/PyTeal 1.0+
- AlgoNode API access
- 1+ Algo per contract creation

```bash
# 1. Install dependencies
pip install algosdk algopy pyteal

# 2. Set ALGO network endpoints
export ALGONODE_TOKEN="YOUR_TOKEN"
export ALGO_SERVER="https://mainnet-api.algonode.cloud"

# 3. Deploy BONAFIDE suite
python3 algorand/deploy_bonafide.py \
  --network mainnet \
  --deployer-mnemonic "YOUR 25-WORD SEED" \
  --contracts genius,bonatoken,tabularium,fides,sponsiopactum,censura,senatus,tessera,aerarium
```

**Contract Deployment Order** (dependencies):
```
1. Genius (base logic)        [App ID TBD]
2. Tabularium (records)       [App ID TBD]
3. BonaToken (tokens)         [App ID TBD]
4. Fides (reputation)         [App ID TBD]
5. SponsioPactum (patronage)  [App ID TBD]
6. Censura (audits)           [App ID TBD]
7. Senatus (voting)           [App ID TBD]
8. Tessera (escrow)           [App ID TBD]
9. Aerarium (treasury)        [App ID TBD]
```

### 3.2 x402 Gating on Algorand (PARSEC/GoPlausible)

**PARSEC x402-avm Runtime**:

```python
from parsec import x402_avm, PaymentRail, DebasementIndexV2

# Initialize PARSEC runtime
runtime = x402_avm.Runtime(
    app_id=TBD,  # x402 gating app
    token_bucket_capacity=1000,
    tokens_per_second=10,
    fee_model=DebasementIndexV2(
        launch_time=current_time,
        daily_deflate_factor=10**12,
        min_deflate_factor=10**17
    )
)

# Register content-addressable resource
runtime.register_resource(
    content_hash=sha256(b"content"),
    price=1000,  # microAlgo
    duration=3600  # 1 hour access
)
```

### 3.3 USDC ASA Bridging

**ASA ID**: 31566704 (USDC on Algorand)

```python
# Opt-in user to USDC ASA
from algosdk import asa

client.algod.send_payment(
    sender=user_address,
    receiver=payment_rail_address,
    amount=1000000,  # 1 USDC (6 decimals)
    asset_index=31566704
)
```

---

## Phase 4: DAIO Governance Initialization (Week 4)

### 4.1 Senatus (Senate) Setup

**Initial Senators** (7-of-11 multisig):
- Gregory L. (codephreak) — Lead architect
- 3x EVM security auditors
- 3x Algorand validators
- 3x Governance delegates (community)
- 2x Observers (advisors, no vote)

```bash
# Deploy Senatus
forge script script/Deploy.s.sol:SenatusDeploy \
  --rpc-url https://eth.llamarpc.com \
  --broadcast

# Configure senators
cast send 0x<Senatus> \
  "addSenator(address)" \
  0x<address1> 0x<address2> ... 0x<address7>
```

### 4.2 War Council (Sun Tzu 13-Chapter Assessment)

**Deployment**:
```bash
forge script script/Deploy.s.sol:WarCouncilScript \
  --rpc-url https://eth.llamarpc.com \
  --broadcast
```

**Assessment Categories** (Sun Tzu):
1. **Laying Plans**: System design review
2. **Waging War**: Payment flow security
3. **Attack by Stratagem**: Fee optimization
4. **Tactical Dispositions**: Rate limiting
5. **Energy**: Validator performance
6. **Weak Points & Strong**: Risk assessment
7. **Maneuvering**: Cross-chain routing
8. **Variation in Tactics**: Governance flexibility
9. **The Distribution of Troops**: Resource allocation
10. **Terrain**: Multi-chain coordination
11. **The Nine Situations**: Edge case handling
12. **The Attack by Fire**: Emergency procedures
13. **The Use of Spies**: Monitoring/alerts

### 4.3 Curia (Assembly) & Censura (Auditors)

```bash
# Deploy Curia (executive proposals)
forge script script/Deploy.s.sol:CuriaScript \
  --rpc-url https://eth.llamarpc.com \
  --broadcast

# Deploy Censura (audit log)
forge script script/Deploy.s.sol:CensuraScript \
  --rpc-url https://eth.llamarpc.com \
  --broadcast

# Link all governance components
cast send 0x<Senatus> \
  "setCuria(address)" 0x<Curia>
cast send 0x<Senatus> \
  "setCensura(address)" 0x<Censura>
```

### 4.4 Transfer Ownership to Senatus

**CRITICAL**: Renounce deployer keys (immutable post-deploy)

```bash
# Transfer PaymentRail ownership to Senatus multisig
cast send 0x<PaymentRail> \
  "transferOwnership(address)" \
  0x<Senatus>

# Verify ownership transfer
cast call 0x<PaymentRail> "owner()" | grep 0x<Senatus>

# Renounce all deployer privileges
# From this point forward: no admin keys, no upgrades, immutable
```

---

## Phase 5: Bridge Activation (Week 5)

### 5.1 Cross-Chain Messaging Setup

**Option A: LayerZero (OFT V2)**

```bash
# Set up LayerZero endpoints
export LZ_ENDPOINT_ETH="0x1a44076050125825900e73fcFDD7C3DirtyFoo"
export LZ_ENDPOINT_0G="0x..."

# Deploy OFT token on both chains
forge script script/Deploy.s.sol:OFTScript \
  --rpc-url https://eth.llamarpc.com \
  --broadcast

forge script script/Deploy.s.sol:OFTScript \
  --rpc-url https://aristotle-rpc.zerogwei.com \
  --broadcast

# Configure cross-chain paths
cast send 0x<OFT> \
  "setPeer(uint32,bytes32)" \
  30109 0x<OFT_0G>  # 0G Aristotle peer ID
```

**Option B: Native Bridge (if preferred)**

Implement cross-chain bridge via validator consensus in ValidatorRegistry.

### 5.2 Bridge Health Check

```bash
# Test transfer: Ethereum → 0G Aristotle
forge script script/BridgeTest.s.sol \
  --rpc-url https://eth.llamarpc.com \
  --broadcast

# Monitor confirmations
# - Ethereum: 12+ blocks (~3 min)
# - 0G: 4+ blocks (~12 sec)
# - Algorand: 4+ rounds (~4 sec)
```

**Expected Output**:
```
✓ 100 test transfers initiated
✓ 98 settled on destination (98% success rate acceptable)
✓ Average latency: Ethereum 3:15min, 0G 45sec, Algorand 8sec
✓ Fee distribution: 30% validators, 70% treasury
```

---

## Phase 6: Agent Marketplace Integration (Week 5.5)

### 6.1 Update ChainRegistry

```bash
# Populate allchain.html registry (2,500+ chains)
# Endpoint: agenticplace.pythai.net/chains/register

curl -X POST https://agenticplace.pythai.net/api/chains/register \
  -H "Authorization: Bearer $SENATUS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "chains": [
      {
        "chainId": 1,
        "name": "Ethereum Mainnet",
        "rpcUrl": "https://eth.llamarpc.com",
        "erc8004Registry": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
        "paymentRail": "0x<PaymentRail>",
        "isActive": true
      },
      {
        "chainId": 16661,
        "name": "0G Aristotle",
        "rpcUrl": "https://aristotle-rpc.zerogwei.com",
        "erc8004Registry": "0x<0G_REGISTRY>",
        "paymentRail": "0x<0G_PAYMENT_RAIL>",
        "isActive": true
      }
    ]
  }'
```

### 6.2 Agent Discovery Activation

```bash
# Enable agent registration with x402 gating
curl -X POST https://agenticplace.pythai.net/api/agents/enable-x402 \
  -H "Authorization: Bearer $SENATUS_TOKEN"

# Test agent registration
curl -X POST https://agenticplace.pythai.net/api/agents/register \
  -d '{
    "name": "test-agent-1",
    "apiUrl": "https://test-agent-1.local/api",
    "x402RateLimit": 1000,
    "requiredToken": "PYTHAI",
    "pricePerRequest": "0.001 PYTHAI"
  }'
```

---

## Phase 7: mindX API Gateway Activation (Week 6)

### 7.1 Deploy API Gateway

**FastAPI + WebSocket Server**

```bash
# Install dependencies
pip install fastapi uvicorn websockets pydantic algosdk web3

# Start API server
python3 api/rails_api.py \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 32 \
  --log-level INFO

# Expected endpoints (350+)
# GET  /api/v1/payments/{receiptId}
# POST /api/v1/payments/process
# GET  /api/v1/identities/{did}
# GET  /api/v1/agents/discover
# WS   /ws/rate-limit/{userAddr}
# ... (see api/openapi.yaml for full spec)
```

### 7.2 Enable CAIP-122 Authentication

```python
from mindx_rails import caip122

@app.middleware("http")
async def verify_caip122(request: Request, call_next):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    
    # Parse: "CAIP-122 <address>:<signature>"
    parts = auth_header.split()
    if len(parts) != 2 or parts[0] != "CAIP-122":
        return JSONResponse({"error": "invalid_auth"}, status_code=401)
    
    address, signature = parts[1].split(":")
    
    # Verify signature
    verified = await caip122.verify(
        message=request.url.path,
        signature=signature,
        address=address
    )
    
    if not verified:
        return JSONResponse({"error": "invalid_signature"}, status_code=401)
    
    return await call_next(request)
```

### 7.3 Rate Limiting with WebSocket

```typescript
// Client-side rate limit monitoring
const ws = new WebSocket(
  `wss://mindx.pythai.net/ws/rate-limit/${userAddress}`
);

ws.onmessage = (event) => {
  const { tokensRemaining, bucketCapacity } = JSON.parse(event.data);
  console.log(`Rate limit: ${tokensRemaining}/${bucketCapacity}`);
};
```

---

## Phase 8: Post-Quantum Readiness (Week 6.5)

### 8.1 Falcon-1024 Key Generation (0G Aristotle)

```bash
# Generate post-quantum keypairs for validators
# Using liboqs-python or Kyber library

python3 deployment/generate_pqc_keys.py \
  --algorithm falcon1024 \
  --count 4 \  # 1R + 3V
  --output-dir ./keys/pqc

# Output:
# keys/pqc/validator-1r.falcon.pk (public key)
# keys/pqc/validator-1r.falcon.sk (private key, HSM-stored)
# keys/pqc/validator-3v-{1,2,3}.falcon.{pk,sk}
```

### 8.2 Deploy EIP-8141 Frame Precompiles (0G Aristotle)

**If not already in 0G mainnet**:

```bash
# 1. Build precompile module
cd 0g-go/core/vm
go build -o falcon_precompile.so falcon_precompile.go

# 2. Patch 0G Aristotle client
# Merge EIP-8051/8052 precompiles into bytecode

# 3. Restart 0G validators with new binary
systemctl restart 0g-validator

# 4. Verify precompiles are active
cast call 0x0c "verifyFalcon(bytes,bytes,bytes)" \
  --rpc-url https://aristotle-rpc.zerogwei.com
```

### 8.3 Enable Frame Transactions (Type 0x06)

```python
from web3 import Web3
from eth_keys import keys
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import falcon

# Generate Frame Transaction
frame_tx = {
    "type": 0x06,
    "chainId": 16661,
    "nonce": 0,
    "maxFeePerGas": 1000000000,
    "maxPriorityFeePerGas": 1000000000,
    "gasLimit": 21000,
    "to": "0x...",
    "value": 0,
    "data": b"",
    "cfi": b"hegota-fork-v1"  # Crypto Foundation Identifier
}

# Sign with Falcon-1024
private_key = falcon.generate_private_key()
signature = private_key.sign(
    Web3.keccak(text=str(frame_tx)),
    algorithm=None  # Falcon uses internal padding
)

frame_tx["sig"] = signature
frame_tx["pubKey"] = private_key.public_key().public_bytes(...)

# Send Frame Transaction
receipt = w3.eth.send_raw_transaction(
    w3.eth.account.encode_transaction(frame_tx)
)
```

---

## Phase 9: Monitoring & Activation (Week 7)

### 9.1 Deploy Monitoring Stack

```bash
# Prometheus + Grafana for metrics

docker-compose -f deployment/docker-compose.yml up -d

# Metrics to monitor:
# - Payment success rate (target: >99%)
# - Settlement latency (target: <5 min Ethereum, <1 min Algorand)
# - Rate limit violations (target: <0.1%)
# - Validator consensus failures (target: 0%)
# - Treasury balance (track fees accumulated)
```

### 9.2 Health Checks

```bash
# Ethereum PaymentRail
./deployment/scripts/health_check.sh ethereum

# Algorand BONAFIDE
./deployment/scripts/health_check.sh algorand

# 0G Aristotle
./deployment/scripts/health_check.sh 0g

# Expected output: "HEALTHY" on all checks
```

### 9.3 Go Live (Canary, then Full)

**Canary Phase** (Week 7):
```bash
# Enable for 1% of agents
# Monitor for 48 hours
# Metrics: <0.1% error rate

cast send 0x<X402Gate> \
  "setCanaryPercentage(uint8)" 1
```

**Ramp Phase** (Weeks 7-8):
```bash
# Increase to 10% → 50% → 100% agents
# over 2 weeks, with daily monitoring
```

**Full Production** (Week 8+):
```bash
# All agents using x402 payment rails
# Begin post-deployment optimizations
```

---

## Rollback Procedures

### If Critical Issue Detected

```bash
# 1. Pause payment processing
cast send 0x<PaymentRail> "pause()" --private-key $SENATUS_MULTISIG_KEY

# 2. Refund pending payments
forge script script/EmergencyRefund.s.sol \
  --rpc-url https://eth.llamarpc.com \
  --broadcast

# 3. Notify stakeholders
# - Email validators
# - Post to rage.pythai.net
# - Update agenticplace.pythai.net status

# 4. Debug & fix
# - Review contracts for security issues
# - Re-audit with external firm
# - Create patched version

# 5. Redeploy only after Senatus approval
cast send 0x<Senatus> \
  "approveEmergencyRedeployment(bytes32)" \
  0x<proposalHash>
```

---

## Checklist

- [ ] Validator registry deployed with 1R+3V consensus
- [ ] PaymentRail, X402Gate, EAIO on Ethereum mainnet
- [ ] L1Escrow + L2MinterBurner deployed on all chains
- [ ] BONAFIDE suite live on Algorand (9 contracts)
- [ ] x402-avm gating functional (PARSEC runtime)
- [ ] DAIO governance initialized (Senatus 7-of-11)
- [ ] Bridge health checks passing (>98% success rate)
- [ ] ChainRegistry updated with 2,500+ chains
- [ ] mindX API gateway running with 350+ routes
- [ ] CAIP-122 authentication verified (5-chain support)
- [ ] Falcon-1024 keys distributed to validators
- [ ] EIP-8141 precompiles enabled on 0G Aristotle
- [ ] Monitoring stack live (Prometheus/Grafana)
- [ ] Canary phase at <0.1% error rate
- [ ] Full production launch approved by Senatus

---

## Support

- **Emergency Contact**: Gregory L. (codephreak) — codephreak@pythai.net
- **Technical Lead**: CTO of PYTHAI/DELTAVERSE
- **Audit Trail**: rage.pythai.net (all deployments logged)
- **Governance**: senatus.pythai.net (voting interface)

---

*Deployment guide v1.0.0 — cypherpunk2048 standard*
*No upgradeable proxies. Mainnet only. Immutable governance.*
