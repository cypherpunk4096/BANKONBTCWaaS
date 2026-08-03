// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {LogoRegistry} from "../src/LogoRegistry.sol";

contract Treasury {
    receive() external payable {}
}

/// Well-behaved SPINTRADE listing desk listener.
contract SpintradeDesk {
    uint64 public lastChainId;
    string public lastName;
    address public lastRegistrar;
    uint256 public notifications;

    function onChainOnboarded(uint64 chainId, string calldata chainName, address registrar)
        external
    {
        lastChainId = chainId;
        lastName = chainName;
        lastRegistrar = registrar;
        notifications++;
    }
}

/// Hostile listener — must never be able to block onboarding.
contract RevertingDesk {
    function onChainOnboarded(uint64, string calldata, address) external pure {
        revert("spintrade down");
    }
}

/// Gas-griefing listener — bounded call must contain it.
contract GriefingDesk {
    function onChainOnboarded(uint64, string calldata, address) external pure {
        uint256 x;
        for (uint256 i; i < type(uint256).max; ++i) x += i; // burn everything
    }
}

contract GovernanceAndChainsTest is Test {
    LogoRegistry plr;
    Treasury aerarium;
    SpintradeDesk desk;

    address senatus = makeAddr("senatus");
    address mindx = makeAddr("mindx");
    address mindx2 = makeAddr("mindx2");
    address alice = makeAddr("alice");
    address mallory = makeAddr("mallory");

    uint256 constant SUB_FEE = 0.05 ether;
    uint256 constant UPD_FEE = 0.02 ether;
    uint256 constant CHAIN_FEE = 1 ether;

    function setUp() public {
        aerarium = new Treasury();
        desk = new SpintradeDesk();
        plr = new LogoRegistry(
            senatus, mindx, address(aerarium), address(desk),
            SUB_FEE, UPD_FEE, CHAIN_FEE, "https://rage.pythai.net/plr/"
        );
        vm.deal(alice, 100 ether);
        vm.deal(mallory, 100 ether);
    }

    // ------------------------------------------------------- validator rotation

    function test_senatus_rotatesValidator() public {
        vm.prank(senatus);
        plr.rotateValidator(mindx2);
        assertEq(plr.validator(), mindx2);

        // old validator loses power, new one gains it
        vm.prank(alice);
        uint256 id = plr.submitLogo{value: SUB_FEE}(1, address(0xBEEF), keccak256("l"), "cid");
        vm.prank(mindx);
        vm.expectRevert(LogoRegistry.NotValidator.selector);
        plr.assess(id, "ar://l");
        vm.prank(mindx2);
        plr.assess(id, "ar://l");
    }

    function test_rotation_notSenatus_reverts() public {
        vm.prank(mindx); // even the validator itself cannot self-rotate
        vm.expectRevert(LogoRegistry.NotSenatus.selector);
        plr.rotateValidator(mindx2);
        vm.prank(mallory);
        vm.expectRevert(LogoRegistry.NotSenatus.selector);
        plr.rotateValidator(mallory);
    }

    function test_rotation_toZero_reverts() public {
        vm.prank(senatus);
        vm.expectRevert(LogoRegistry.ZeroAddress.selector);
        plr.rotateValidator(address(0));
    }

    function test_senatus_cannotTouchEntries() public {
        vm.prank(alice);
        uint256 id = plr.submitLogo{value: SUB_FEE}(1, address(0xBEEF), keccak256("l"), "cid");
        vm.prank(senatus); // governance ratifies; it does not assess art
        vm.expectRevert(LogoRegistry.NotValidator.selector);
        plr.assess(id, "ar://x");
    }

    // ---------------------------------------------------------- chain onboarding

    function test_onboardChain_paysFee_recordsChain_notifiesSpintrade() public {
        vm.prank(alice);
        plr.onboardChain{value: CHAIN_FEE}(747474, "NEWCHAIN", "https://rpc.newchain.xyz");

        assertEq(address(aerarium).balance, CHAIN_FEE);
        assertEq(plr.chainCount(), 1);
        (string memory nm,, address registrar,, bool active) = plr.chains(747474);
        assertEq(nm, "NEWCHAIN");
        assertEq(registrar, alice);
        assertTrue(active);

        // SPINTRADE knew first — same transaction
        assertEq(desk.notifications(), 1);
        assertEq(desk.lastChainId(), 747474);
        assertEq(desk.lastRegistrar(), alice);
    }

    function test_onboardChain_wrongFee_reverts() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(LogoRegistry.WrongFee.selector, 1 wei, CHAIN_FEE));
        plr.onboardChain{value: 1 wei}(1, "x", "y");
    }

    function test_onboardChain_duplicate_reverts() public {
        vm.startPrank(alice);
        plr.onboardChain{value: CHAIN_FEE}(42, "A", "rpcA");
        vm.expectRevert(abi.encodeWithSelector(LogoRegistry.ChainExists.selector, uint64(42)));
        plr.onboardChain{value: CHAIN_FEE}(42, "B", "rpcB");
        vm.stopPrank();
    }

    function test_revertingSpintrade_cannotBlockOnboarding() public {
        RevertingDesk bad = new RevertingDesk();
        LogoRegistry plr2 = new LogoRegistry(
            senatus, mindx, address(aerarium), address(bad),
            SUB_FEE, UPD_FEE, CHAIN_FEE, "u/"
        );
        vm.prank(alice);
        plr2.onboardChain{value: CHAIN_FEE}(99, "C", "rpc"); // must succeed
        assertEq(plr2.chainCount(), 1);
    }

    function test_gasGriefingSpintrade_isContained() public {
        GriefingDesk grief = new GriefingDesk();
        LogoRegistry plr2 = new LogoRegistry(
            senatus, mindx, address(aerarium), address(grief),
            SUB_FEE, UPD_FEE, CHAIN_FEE, "u/"
        );
        vm.prank(alice);
        plr2.onboardChain{value: CHAIN_FEE}(100, "D", "rpc"); // bounded 200k gas push
        assertEq(plr2.chainCount(), 1);
    }

    function test_eoaSpintrade_skipsPushCleanly() public {
        // desk in main setUp is a contract; here confirm EOA target is a no-op
        LogoRegistry plr2 = new LogoRegistry(
            senatus, mindx, address(aerarium), makeAddr("eoaDesk"),
            SUB_FEE, UPD_FEE, CHAIN_FEE, "u/"
        );
        vm.prank(alice);
        plr2.onboardChain{value: CHAIN_FEE}(101, "E", "rpc");
        assertEq(plr2.chainCount(), 1);
    }

    // ------------------------------------------------------------ chain status

    function test_validator_togglesChainActive() public {
        vm.prank(alice);
        plr.onboardChain{value: CHAIN_FEE}(7, "F", "rpc");
        vm.prank(mindx);
        plr.setChainActive(7, false);
        (,,,, bool active) = plr.chains(7);
        assertFalse(active);
    }

    function test_setChainActive_unknown_reverts() public {
        vm.prank(mindx);
        vm.expectRevert(abi.encodeWithSelector(LogoRegistry.ChainUnknown.selector, uint64(404)));
        plr.setChainActive(404, false);
    }

    function test_setChainActive_notValidator_reverts() public {
        vm.prank(alice);
        plr.onboardChain{value: CHAIN_FEE}(8, "G", "rpc");
        vm.prank(mallory);
        vm.expectRevert(LogoRegistry.NotValidator.selector);
        plr.setChainActive(8, false);
    }

    // ------------------------------------------------------------------ fuzz

    function testFuzz_onboard_thenLogoOnThatChain(uint64 chainId, address token) public {
        vm.assume(chainId != 0 && token != address(0));
        vm.prank(alice);
        plr.onboardChain{value: CHAIN_FEE}(chainId, "Z", "rpc");
        vm.prank(alice);
        uint256 id = plr.submitLogo{value: SUB_FEE}(chainId, token, keccak256("z"), "cid");
        vm.prank(mindx);
        plr.assess(id, "ar://z");
        vm.prank(senatus);
        plr.ratify(id);
        (bool verified,,,) = plr.canonicalLogo(chainId, token);
        assertTrue(verified);
    }
}
