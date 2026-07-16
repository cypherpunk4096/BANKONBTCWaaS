// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {LogoRegistry} from "../src/LogoRegistry.sol";

contract Aerarium {
    receive() external payable {}
}

contract RejectingTreasury {
    receive() external payable { revert("no"); }
}

contract LogoRegistryTest is Test {
    LogoRegistry plr;
    address senatus = makeAddr("senatus");
    address mindx = makeAddr("mindxValidator");
    address spintrade = makeAddr("spintradeEOA");
    Aerarium aerarium;
    address alice = makeAddr("alice");
    address mallory = makeAddr("mallory");

    uint256 constant SUB_FEE = 0.05 ether;
    uint256 constant UPD_FEE = 0.02 ether;
    uint256 constant CHAIN_FEE = 1 ether;

    uint64 constant CHAIN = 1;
    address constant PAI = address(0xBEEF);

    function setUp() public {
        aerarium = new Aerarium();
        plr = new LogoRegistry(
            senatus,
            mindx,
            address(aerarium),
            spintrade,
            SUB_FEE,
            UPD_FEE,
            CHAIN_FEE,
            "https://rage.pythai.net/plr/"
        );
        vm.deal(alice, 10 ether);
        vm.deal(mallory, 10 ether);
    }

    // ------------------------------------------------------------ submission

    function test_submit_mintsINFT_andForwardsFee() public {
        vm.prank(alice);
        uint256 id = plr.submitLogo{value: SUB_FEE}(CHAIN, PAI, keccak256("logo-v1"), "arweave://cid1");

        assertEq(id, 1);
        assertEq(plr.ownerOf(1), alice);
        assertEq(address(aerarium).balance, SUB_FEE);

        (bool verified,,,) = plr.canonicalLogo(CHAIN, PAI);
        assertFalse(verified); // pending until mindX validates
    }

    function test_submit_wrongFee_reverts() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(LogoRegistry.WrongFee.selector, 1 wei, SUB_FEE));
        plr.submitLogo{value: 1 wei}(CHAIN, PAI, keccak256("x"), "cid");
    }

    function test_submit_duplicate_reverts() public {
        vm.startPrank(alice);
        plr.submitLogo{value: SUB_FEE}(CHAIN, PAI, keccak256("a"), "cidA");
        vm.expectRevert(abi.encodeWithSelector(LogoRegistry.AlreadyRegistered.selector, CHAIN, PAI));
        plr.submitLogo{value: SUB_FEE}(CHAIN, PAI, keccak256("b"), "cidB");
        vm.stopPrank();
    }

    function test_sameToken_differentChain_isDistinct() public {
        vm.startPrank(alice);
        uint256 a = plr.submitLogo{value: SUB_FEE}(1, PAI, keccak256("eth"), "cidE");
        uint256 b = plr.submitLogo{value: SUB_FEE}(8453, PAI, keccak256("base"), "cidB");
        vm.stopPrank();
        assertTrue(a != b);
    }

    function test_feeForward_toRejectingTreasury_reverts() public {
        RejectingTreasury bad = new RejectingTreasury();
        LogoRegistry plr2 = new LogoRegistry(senatus, mindx, address(bad), spintrade, SUB_FEE, UPD_FEE, CHAIN_FEE, "u/");
        vm.prank(alice);
        vm.expectRevert(LogoRegistry.TreasuryTransferFailed.selector);
        plr2.submitLogo{value: SUB_FEE}(CHAIN, PAI, keccak256("x"), "cid");
    }

    // ------------------------------------------------------------ validation

    function _submitAndValidate() internal returns (uint256 id) {
        vm.prank(alice);
        id = plr.submitLogo{value: SUB_FEE}(CHAIN, PAI, keccak256("logo-v1"), "ar://placeholder");
        vm.prank(mindx);
        plr.assess(id, "ar://cid1");
        vm.prank(senatus);
        plr.ratify(id);
    }

    function test_assess_onlyMindX_ratify_onlySenatus() public {
        vm.prank(alice);
        uint256 id = plr.submitLogo{value: SUB_FEE}(CHAIN, PAI, keccak256("x"), "cid");
        vm.prank(mallory);
        vm.expectRevert(LogoRegistry.NotValidator.selector);
        plr.assess(id, "ar://x");
        vm.prank(mindx);
        plr.assess(id, "ar://x");
        vm.prank(mallory);
        vm.expectRevert(LogoRegistry.NotSenatus.selector);
        plr.ratify(id);
        // even mindX cannot ratify — DAIO acceptance is mandatory
        vm.prank(mindx);
        vm.expectRevert(LogoRegistry.NotSenatus.selector);
        plr.ratify(id);
    }

    function test_pipeline_setsCanonical_withArweaveCID() public {
        uint256 id = _submitAndValidate();
        (bool verified, bytes32 h, string memory cid, uint32 rev) = plr.canonicalLogo(CHAIN, PAI);
        assertTrue(verified);
        assertEq(h, keccak256("logo-v1"));
        assertEq(cid, "ar://cid1"); // permanent CID replaced the placeholder
        assertEq(rev, 0);
        assertEq(plr.ownerOf(id), alice);
    }

    function test_assess_emptyCID_reverts() public {
        vm.prank(alice);
        uint256 id = plr.submitLogo{value: SUB_FEE}(CHAIN, PAI, keccak256("x"), "cid");
        vm.prank(mindx);
        vm.expectRevert(LogoRegistry.EmptyCID.selector);
        plr.assess(id, "");
    }

    function test_daio_canReject_backToPending() public {
        vm.prank(alice);
        uint256 id = plr.submitLogo{value: SUB_FEE}(CHAIN, PAI, keccak256("x"), "cid");
        vm.prank(mindx);
        plr.assess(id, "ar://x");
        vm.prank(senatus);
        plr.reject(id, "brand dispute");
        // re-assessment possible without a new fee
        vm.prank(mindx);
        plr.assess(id, "ar://x2");
        vm.prank(senatus);
        plr.ratify(id);
        (bool verified,, string memory cid,) = plr.canonicalLogo(CHAIN, PAI);
        assertTrue(verified);
        assertEq(cid, "ar://x2");
    }

    function test_assess_nonPending_reverts() public {
        uint256 id = _submitAndValidate();
        vm.prank(mindx);
        vm.expectRevert(
            abi.encodeWithSelector(LogoRegistry.BadStatus.selector, LogoRegistry.Status.Verified)
        );
        plr.assess(id, "ar://again");
    }

    // --------------------------------------------------------------- updates

    function test_paidUpdate_reentersPending_thenRevalidates() public {
        uint256 id = _submitAndValidate();

        vm.prank(alice);
        plr.updateLogo{value: UPD_FEE}(CHAIN, PAI, keccak256("logo-v2"), "arweave://cid2");

        (bool verified,,, uint32 rev) = plr.canonicalLogo(CHAIN, PAI);
        assertFalse(verified); // unreviewed art never serves as canonical
        assertEq(rev, 1);
        assertEq(address(aerarium).balance, SUB_FEE + UPD_FEE);

        vm.prank(mindx);
        plr.assess(id, "ar://cid2");
        (verified,,,) = plr.canonicalLogo(CHAIN, PAI);
        assertFalse(verified); // assessed but not yet DAIO-accepted
        vm.prank(senatus);
        plr.ratify(id);
        (verified,,,) = plr.canonicalLogo(CHAIN, PAI);
        assertTrue(verified);
    }

    function test_update_unregistered_reverts() public {
        vm.prank(alice);
        vm.expectRevert(LogoRegistry.NotRegistered.selector);
        plr.updateLogo{value: UPD_FEE}(CHAIN, address(0xDEAD), keccak256("x"), "cid");
    }

    function test_evolve_requiresDaioAcceptance() public {
        uint256 id = _submitAndValidate();
        vm.prank(mindx);
        vm.expectEmit(false, false, false, true);
        emit LogoRegistry.MetadataUpdate(id);
        plr.evolve(id, keccak256("logo-evolved"), "ar://cidEvo");

        (bool verified,,,) = plr.canonicalLogo(CHAIN, PAI);
        assertFalse(verified); // evolution is assessed, not yet accepted

        vm.prank(senatus);
        plr.ratify(id);
        (verified, , , ) = plr.canonicalLogo(CHAIN, PAI);
        bytes32 h; uint32 rev;
        (verified, h, , rev) = plr.canonicalLogo(CHAIN, PAI);
        assertTrue(verified);
        assertEq(h, keccak256("logo-evolved"));
        assertEq(rev, 1);
    }

    // ------------------------------------------------------------ revocation

    function test_revoke_removesCanonicalStatus() public {
        uint256 id = _submitAndValidate();
        vm.prank(mindx);
        plr.revoke(id, "impersonation");
        (bool verified,,,) = plr.canonicalLogo(CHAIN, PAI);
        assertFalse(verified);
    }

    function test_revoked_cannotUpdate() public {
        uint256 id = _submitAndValidate();
        vm.prank(mindx);
        plr.revoke(id, "rug");
        vm.prank(alice);
        vm.expectRevert(
            abi.encodeWithSelector(LogoRegistry.BadStatus.selector, LogoRegistry.Status.Revoked)
        );
        plr.updateLogo{value: UPD_FEE}(CHAIN, PAI, keccak256("x"), "cid");
    }

    // ------------------------------------------------------------------ misc

    function test_tokenURI() public {
        uint256 id = _submitAndValidate();
        assertEq(plr.tokenURI(id), "https://rage.pythai.net/plr/1");
    }

    function test_supportsInterface() public view {
        assertTrue(plr.supportsInterface(0x80ac58cd)); // 721
        assertTrue(plr.supportsInterface(0x49064906)); // 4906
    }

    function test_iNFT_transferable_validationUnaffected() public {
        uint256 id = _submitAndValidate();
        vm.prank(alice);
        plr.transferFrom(alice, mallory, id);
        assertEq(plr.ownerOf(id), mallory);
        (bool verified,,,) = plr.canonicalLogo(CHAIN, PAI);
        assertTrue(verified); // ownership of proof != control of validation
    }

    // ------------------------------------------------------------------ fuzz

    function testFuzz_keying_isCollisionFreePerPair(uint64 c1, uint64 c2, address t1, address t2)
        public
        view
    {
        vm.assume(c1 != c2 || t1 != t2);
        assertTrue(plr.entryKey(c1, t1) != plr.entryKey(c2, t2));
    }

    function testFuzz_submit_exactFeeOnly(uint96 sent) public {
        vm.assume(sent != SUB_FEE);
        vm.deal(alice, uint256(sent) + 1 ether);
        vm.prank(alice);
        vm.expectRevert();
        plr.submitLogo{value: sent}(CHAIN, PAI, keccak256("x"), "cid");
    }
}
