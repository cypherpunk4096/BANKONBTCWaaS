# PYTHAI / BANKON Drawbridge — build & deploy targets
.PHONY: install build test fuzz invariant fmt clean salts deploy-troll deploy-bridge

install:
	forge install foundry-rs/forge-std --no-commit
	forge install transmissions11/solmate --no-commit
	forge install LayerZero-Labs/devtools --no-commit
	forge install pyth-network/pyth-sdk-solidity --no-commit

build:      ; forge build
test:       ; forge test -vv
fuzz:       ; forge test --fuzz-runs 10000 -vv
invariant:  ; forge test --match-path "test/*invariants*" -vvv
fmt:        ; forge fmt
clean:      ; forge clean

# Print the canonical salts + predicted CREATE2 addresses (same on every chain).
salts:
	@echo "tollkeeper : $$(cast keccak 'pythai.net/tollkeeper/v1')"
	@echo "royalt     : $$(cast keccak 'bankon.eth/royalt/v1')"
	@echo "drawbridge : $$(cast keccak 'bankon.eth/drawbridge/v2')"
	@echo "pai        : $$(cast keccak 'bankon.eth/pai/v1')"
	@echo "spring     : $$(cast keccak 'bankon.eth/spring/v1')"
	@echo "toll_dial  : $$(cast keccak 'pythai.net/toll_dial/v1')"
	@echo "collateral : $$(cast keccak 'pythai.net/collateral_factory/v1')"
	@echo "fb_oracle  : $$(cast keccak 'pythai.net/four_bucks_oracle/v1')"

# Example: make deploy-bridge CHAIN=ethereum
deploy-bridge:
	forge script script/DeployDrawbridgeV2.s.sol --rpc-url $(CHAIN) --broadcast --verify
