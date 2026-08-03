"""x402 payment-gated accounting/oracle API for the Drawbridge system.

Python >= 3.12. HTTP 402 (RFC 9110) over the x402 protocol, settled on Algorand
mainnet via GoPlausible x402-avm. Consistent with the PYTHAI x402 rails
(Ethereum mainnet, Algorand mainnet, 0G Aristotle).

    pip install "x402-avm[fastapi,avm]"   # import name: x402
    uvicorn integrations.x402_server:app

Verify package/repo names against https://github.com/GoPlausible and the
parsec / parsec-wallet references before production.
"""

from fastapi import FastAPI

from x402.http import FacilitatorConfig, HTTPFacilitatorClientSync, PaymentMiddlewareASGI
from x402.mechanisms.avm.exact import ExactAvmServerScheme
from x402.server import x402ResourceServerSync

# Algorand mainnet CAIP-2 network identifier
AVM_MAINNET = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="

# TODO(deploy): your Algorand mainnet receiving address (parsec-wallet compatible)
PAY_TO = "YOUR_ALGORAND_ADDR"

facilitator = HTTPFacilitatorClientSync(FacilitatorConfig(url="https://x402.org/facilitator"))
server = x402ResourceServerSync(facilitator)
server.register(AVM_MAINNET, ExactAvmServerScheme())

routes = {
    # PAI reserve & backingOk snapshot (reads the same-address Troll on any chain)
    "GET /accounting/pai/reserve": {
        "accepts": {
            "scheme": "exact",
            "network": AVM_MAINNET,
            "payTo": PAY_TO,
            "price": "$0.01",
        },
        "description": "PAI reserve, moat depth, and backingOk snapshot per chainid",
    },
    # Post a signed 4-CAD reference into the in-house aggregation layer (mindX route)
    "POST /oracle/post": {
        "accepts": {
            "scheme": "exact",
            "network": AVM_MAINNET,
            "payTo": PAY_TO,
            "price": "$0.05",
        },
        "description": "Post a signed 4-CAD reference to the in-house aggregation layer",
    },
    # Golden-toll telemetry for agents (moat/royalty/closure stats via mindX -> rage)
    "GET /accounting/troll/{chainid}": {
        "accepts": {
            "scheme": "exact",
            "network": AVM_MAINNET,
            "payTo": PAY_TO,
            "price": "$0.01",
        },
        "description": "moat, reserve, inFlight, backingOk for the gateway on {chainid}",
    },
}

app = FastAPI(title="PYTHAI Drawbridge accounting API (x402-gated)")
app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
