# PYTHAI x402 Payment Rails
## Complete Integration Architecture v2.0.0

> **v2.0.0 — Defensive Posture Release.** Retires the Falcon-1024 profile for the
> **CP-2048** hybrid, multi-family, crypto-agile standard and closes six HIGH-severity
> exposures (pre-auth DDoS, cross-chain non-atomicity, signature replay, immutability
> "no-pause" trap, validator blast-radius). Start with **`SECURITY_POSTURE.md`**, then
> **`docs/CP-2048.md`**. New artifacts: `foundry/src/pqc/`, `foundry/src/security/`,
> `api/middleware/preauth_shield.py`, `integrations/mindx/saga_coordinator.py`.

**Deployable across**: Algorand (constitutional rail), Ethereum mainnet (EVM anchor), 0G Aristotle (AI settlement)

---

## Executive Summary

The PYTHAI x402 payment rails provide a sovereign, cross-chain payment layer integrating:
- **x402 Algorand**: Payment gating, access control, token-bucket throttling (via GoPlausible x402-avm runtime under PARSEC brand)
- **EVM Bridge**: xERC20 via openBDK (L1Escrow, L2MinterBurner, ValidatorRegistry with 1R+3V BFT)
- **Identity Layer**: BANKON (AlgoIDNFT sovereign identity, dual BANKON PYTHAI/PAI tokens)
- **Agent Marketplace**: AgenticPlace (ERC-8004 registry, live since Jan 29, 2026)
- **AI Orchestration**: mindX (BDI/Darwin-Gödel cognitive architecture, 350+ API routes published at rage.pythai.net)
- **Governance**: DAIO (Roman-republic naming: Senatus, Curia, Censura, WarCouncil, Boardroom, DeadmansSwitch, ChainRegistry)

**Post-Quantum Readiness**: EIP-8141 Frame Transactions (type 0x06), EIP-8051/8052 precompiles (ML-DSA, Falcon-1024)

---

## Architecture Layers

### Layer 1: Constitutional Rail (Algorand AVM)
- **Runtime**: GoPlausible x402-avm (PARSEC brand)
- **Tokens**: USDC ASA 31566704, custom x402-gated ASAs
- **Primitives**: CAIP-2 `algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=`
- **Contracts**: BONAFIDE (9-contract suite: Genius, BonaToken, Tabularium, Fides, SponsioPactum, Censura, Senatus, Tessera, Aerarium)

### Layer 2: EVM Settlement (Ethereum + 0G Aristotle)
- **Primary**: Ethereum mainnet
- **AI Settlement**: 0G Aristotle (chain ID 16661)
- **Bridge**: openBDK (L1Escrow + L2MinterBurner + ValidatorRegistry)
- **Identity Registry**: `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` (ERC-8004)
- **Tokens**: xERC20 via Pontifex sovereignty layer (Pons, Custodia, Omnipons, PontemAgglayer)

### Layer 3: Identity & Payment (BANKON)
- **Sovereign Identity**: AlgoIDNFT on Algorand
- **Payment Layer**: SATPAY bridge (1:1 satoshi-pegged, 2.1 quadrillion BKS supply)
- **Governance Tokens**: BANKON PYTHAI (111,111.111 fixed supply, LayerZero OFT V2)
- **Access Gate**: X402AccessGate (DAIO role-gating)

### Layer 4: Agent Marketplace (AgenticPlace)
- **Registry**: ERC-8004 (`0x8004A169FB4a3325136EB29fA0ceB6D2e539a432`)
- **Chain Mapping**: Live registry at agenticplace.pythai.net/allchain.html (2,500+ EVM chains)
- **Payment Acceptance**: x402-gated agents, direct ETH/token settlement

### Layer 5: AI Orchestration (mindX)
- **Deployment**: mindx.pythai.net (production)
- **API Routes**: 350+ (BDI inference, Darwin-Gödel cognition, RAGE memory, MASTERMIND orchestration)
- **Publication**: rage.pythai.net (Professor Codephreak research)
- **Wallet Integration**: CAIP-122 (EVM + Algorand + Solana + Cardano + Bitcoin)

---

## Technical Stack

```
┌─────────────────────────────────────────────────────┐
│  AgenticPlace (ERC-8004 Marketplace)                 │
│  ├─ Agent Registry                                   │
│  ├─ x402 Payment Gate                               │
│  └─ Chain Mapping (allchain.html)                    │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│  mindX AI Orchestration (mindx.pythai.net)           │
│  ├─ BDI Agent Logic                                 │
│  ├─ Darwin-Gödel Cognition                          │
│  ├─ Payment Rail Integration (REST/WS)              │
│  └─ CAIP-122 Wallet Auth                            │
└────────────────┬────────────────────────────────────┘
                 │
     ┌───────────┼───────────┐
     │           │           │
┌────▼──────┐ ┌─▼────────┐ ┌▼──────────┐
│ Algorand  │ │ Ethereum │ │ 0G        │
│ x402 Rail │ │ Mainnet  │ │ Aristotle │
│ BONAFIDE  │ │ openBDK  │ │ EIP-8141  │
└────┬──────┘ └─┬────────┘ └┬──────────┘
     │         │          │
     └─────────┼──────────┘
               │
        ┌──────▼──────────┐
        │ BANKON          │
        │ ├─ Identity     │
        │ ├─ Governance   │
        │ ├─ Payment      │
        │ └─ Access Gate  │
        └─────────────────┘
```

---

## Deployment Targets

| Layer | Network | Chain ID | Primary Contract | Status |
|-------|---------|----------|------------------|--------|
| Constitutional | Algorand | `algorand:wGHE...` | BONAFIDE suite | Live |
| EVM Anchor | Ethereum | 1 | openBDK (0x8004...) | Live |
| AI Settlement | 0G Aristotle | 16661 | EIP-8141 Frame Tx | Ready |
| Payment | BANKON | Multi-chain | SATPAYBridge | Live |

---

## File Structure

```
pythai-x402-rails/
├── README.md                          # This file
├── ARCHITECTURE.md                    # Detailed technical spec
├── DEPLOYMENT.md                      # Mainnet deployment guide
├── LIMITATIONS.md                     # Known constraints
├── foundry/
│   ├── foundry.toml                   # Foundry config
│   ├── src/
│   │   ├── core/
│   │   │   ├── PaymentRail.sol        # xERC20 rail entry point
│   │   │   ├── X402AccessGate.sol     # x402 gating logic
│   │   │   └── ChainRegistry.sol      # Multi-chain mapping
│   │   ├── bridge/
│   │   │   ├── L1Escrow.sol           # Ethereum anchoring
│   │   │   ├── L2MinterBurner.sol     # Minting authority
│   │   │   └── ValidatorRegistry.sol  # 1R+3V BFT
│   │   ├── identity/
│   │   │   ├── EAIO.sol               # ERC-8004 implementation
│   │   │   └── BankonIdentity.sol     # AlgoID bridge
│   │   └── governance/
│   │       ├── Senatus.sol            # Senate
│   │       ├── Curia.sol              # Assembly
│   │       ├── Censura.sol            # Auditors
│   │       ├── WarCouncil.sol         # Military
│   │       ├── Boardroom.sol          # Executive
│   │       ├── DeadmansSwitch.sol     # Emergency
│   │       └── ChainRegistry.sol      # Registry
│   ├── test/
│   │   ├── PaymentRail.t.sol
│   │   ├── X402Gate.t.sol
│   │   ├── Bridge.t.sol
│   │   └── Integration.t.sol
│   └── script/
│       ├── Deploy.s.sol
│       ├── Verify.s.sol
│       └── Upgrade.s.sol
├── algorand/
│   ├── bonafide.py                    # BONAFIDE suite (Algopy)
│   ├── parsec_x402.py                 # x402 gating (GoPlausible)
│   ├── wisdom_daemon.py                # x402 content-addressed storage
│   └── tests/
│       └── test_x402.py
├── integrations/
│   ├── agenticplace/
│   │   ├── erc8004_client.ts          # Agent registry client
│   │   ├── marketplace_api.py         # Python wrapper
│   │   └── agent_payment.ts           # Payment acceptance
│   ├── mindx/
│   │   ├── bdi_payment_bridge.py      # BDI ↔ payment rail
│   │   ├── mindx_api_gateway.py       # REST/WS gateway
│   │   └── caip122_wallet.ts          # Multi-chain wallet
│   └── bankon/
│       ├── identity_resolver.py       # AlgoID lookup
│       ├── governance_gate.py         # DAIO access control
│       └── satpay_bridge.py           # Satoshi peg
├── api/
│   ├── openapi.yaml                   # OpenAPI 3.1 spec
│   ├── rails_api.py                   # FastAPI server
│   ├── websocket_server.ts            # Real-time updates
│   └── middleware/
│       ├── x402_auth.py               # Payment validation
│       ├── caip122_auth.ts            # Multi-chain auth
│       └── rate_limiter.py            # Token bucket
├── deployment/
│   ├── docker-compose.yml
│   ├── kubernetes/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── configmap.yaml
│   └── scripts/
│       ├── deploy.sh
│       ├── verify.sh
│       ├── upgrade.sh
│       └── monitor.sh
└── docs/
    ├── QUICKSTART.md
    ├── API.md
    ├── INTEGRATION.md
    └── TROUBLESHOOTING.md
```

---

## Core Concepts

### x402 Payment Gating
- **Standard**: https://httpwg.org/specs/rfc9110.html#status.402
- **Implementation**: GoPlausible x402-avm runtime (Algorand)
- **Primitives**: 
  - Content-addressed ASAs (x402-gated)
  - Token-bucket throttling (POSIX 12-bit permissions)
  - DebasementIndexV2 pricing (deflationary fee model)

### xERC20 Sovereignty Layer (Pontifex)
- **Components**: Pons (entry), Custodia (escrow), Omnipons (aggregator), PontemAgglayer (L2 bridge)
- **No Admin Keys**: Post-deploy immutability
- **1R+3V BFT**: 1 Required validator + 3 optional validators for consensus

### ERC-8004 Identity Registry
- **Live Contract**: `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` (Ethereum)
- **Integration**: BANKON AlgoIDNFT on Algorand
- **Access Control**: DAIO role-based gate (Senatus/Curia authority)

### DAIO Governance
- **Senate (Senatus)**: Legislative authority (0.666 supermajority)
- **Assembly (Curia)**: Executive proposals
- **Auditors (Censura)**: Financial & security oversight
- **War Council**: Sun Tzu 13-chapter assessments (WAGE/SUBDUE/HOLD/WITHDRAW verdicts)
- **CEO + 7 Counsellors**: Emergency powers with 1.2× veto (CISO/CRO)

---

## Quick Integration Reference

### From AgenticPlace
```bash
# Register agent with x402 payment gate
POST /agents/register
{
  "name": "agent-name",
  "apiUrl": "https://your-agent.local/api",
  "x402RateLimit": 1000,
  "requiredToken": "PYTHAI",
  "pricePerRequest": "0.001 PYTHAI"
}
```

### From mindX
```python
# BDI agent accepts x402 payment
from mindx_rails import X402PaymentBridge

bridge = X402PaymentBridge(
    algo_app_id=12345,
    eth_contract="0x...",
    fee_model="debasement_v2"
)

result = await bridge.gated_inference(
    agent_id="agent-uuid",
    input_data={"query": "..."},
    payer_address=CAIP122_addr
)
```

### From BANKON
```typescript
// Access control via DAIO identity
import { X402AccessGate } from "@bankon/access-control";

const gate = new X402AccessGate({
  senatusDaoId: "senatus-contract-id",
  identityRegistry: "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
});

const hasAccess = await gate.checkAccess(
  userAlgoIdNFT,
  requestedResource,
  { role: "agent_deployer" }
);
```

---

## Cypherpunk2048 Standards
- **License**: Apache-2.0 (BANKON copyright)
- **No Admin Keys**: Post-deploy immutability
- **No Upgradeable Proxies**: Flat, versioned contracts
- **Mainnet Only**: Testnet use is sandboxed
- **Build Tool**: Foundry (Solidity), Algopy/PyTeal (Algorand)
- **Naming**: Latin conventions, snake_case code
- **Documentation**: Terse plain-text (.md)

---

## Deployment Checklist

- [ ] Foundry tests passing (100% coverage on PaymentRail, X402Gate, Bridge)
- [ ] Algorand BONAFIDE contracts deployed to mainnet
- [ ] openBDK validators registered (1R+3V consensus)
- [ ] ERC-8004 identity registry confirmed at 0x8004...
- [ ] DAIO governance initialized (Senatus multisig)
- [ ] ChainMapping updated (agenticplace.pythai.net/allchain.html)
- [ ] mindX API endpoints registered (350+ routes live)
- [ ] BANKON identity resolver linked (AlgoID ↔ EVM)
- [ ] Post-quantum keys generated (Falcon-1024 on Algorand)
- [ ] Mainnet monitoring active (Blockscout, Algoexplorer)

---

## Support & Documentation
- **Technical Spec**: See ARCHITECTURE.md
- **Deployment Guide**: See DEPLOYMENT.md
- **API Reference**: See api/openapi.yaml
- **Known Limitations**: See LIMITATIONS.md

---

*Built by Gregory L. (codephreak) for the PYTHAI/DELTAVERSE ecosystem.*
*Cypherpunk2048 standard. No upgradeable proxies. Mainnet only.*
