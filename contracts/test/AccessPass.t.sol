// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Test} from "forge-std/Test.sol";

import {AccessPass} from "../src/AccessPass.sol";

/// @title AccessPass — the paths that are meant to work
/// @notice Everything a paying holder should be able to do. The paths that are
///         meant to fail live in AccessPassAttack.t.sol, where they can be read
///         as a list of defeated attacks rather than lost in among these.
contract AccessPassHappyPathTest is Test {
    AccessPass internal accessPass;

    uint256 internal constant PRICE = 0.01 ether;
    uint256 internal constant DURATION = 30 days;

    address internal owner;
    address internal buyer;
    address internal otherBuyer;

    function setUp() public {
        owner = makeAddr("owner");
        buyer = makeAddr("buyer");
        otherBuyer = makeAddr("otherBuyer");

        // Start at a plausible wall-clock time. Foundry's default is timestamp 1,
        // which makes every expiry in these tests read as a meaningless integer.
        vm.warp(1_767_225_600); // 2026-01-01T00:00:00Z

        vm.prank(owner);
        accessPass = new AccessPass(PRICE, DURATION);

        vm.deal(buyer, 10 ether);
        vm.deal(otherBuyer, 10 ether);
    }

    function test_theTermsAreFixedAtDeployment() public view {
        assertEq(accessPass.owner(), owner, "owner");
        assertEq(accessPass.price(), PRICE, "price");
        assertEq(accessPass.duration(), DURATION, "duration");
    }

    function test_anAddressThatHasNeverPaidHoldsNoPass() public view {
        assertFalse(accessPass.hasValidPass(buyer));
        assertEq(accessPass.expiresAt(buyer), 0, "expiry of a stranger");
    }

    function test_buyingAPassGrantsAccessForTheFullDuration() public {
        uint256 expected = block.timestamp + DURATION;

        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        assertTrue(accessPass.hasValidPass(buyer));
        assertEq(accessPass.expiresAt(buyer), expected, "expiry");
    }

    function test_buyingAPassAnnouncesTheNewExpiry() public {
        uint256 expected = block.timestamp + DURATION;

        // The indexer in iteration 3 rebuilds its projection from this event alone,
        // so the expiry it carries has to be the expiry that was stored.
        vm.expectEmit(true, false, false, true, address(accessPass));
        emit AccessPass.PassPurchased(buyer, expected, PRICE);

        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();
    }

    function test_thePriceIsHeldByTheContractUntilItIsWithdrawn() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        assertEq(address(accessPass).balance, PRICE, "contract balance");
        assertEq(buyer.balance, 10 ether - PRICE, "buyer balance");
    }

    function test_aPassLapsesTheMomentItsExpiryIsReached() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();
        uint256 expiry = accessPass.expiresAt(buyer);

        vm.warp(expiry - 1);
        assertTrue(accessPass.hasValidPass(buyer), "one second before expiry");

        // hasValidPass is a strict comparison, so the expiry second itself is out.
        // An API capping a session at the pass expiry inherits this boundary.
        vm.warp(expiry);
        assertFalse(accessPass.hasValidPass(buyer), "at the expiry second");

        vm.warp(expiry + 1);
        assertFalse(accessPass.hasValidPass(buyer), "after expiry");
    }

    function test_renewingEarlyExtendsFromTheExistingExpiry() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();
        uint256 firstExpiry = accessPass.expiresAt(buyer);

        // A third of the way through, with two thirds still paid for.
        vm.warp(block.timestamp + DURATION / 3);

        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        // The whole point: renewing early must not throw away time already bought.
        assertEq(accessPass.expiresAt(buyer), firstExpiry + DURATION, "extended expiry");
        assertGt(accessPass.expiresAt(buyer), block.timestamp + DURATION, "not reset to now");
    }

    function test_renewingAfterExpiryStartsAfresh() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();
        uint256 firstExpiry = accessPass.expiresAt(buyer);

        vm.warp(firstExpiry + 7 days);
        assertFalse(accessPass.hasValidPass(buyer), "lapsed before renewal");

        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        // A lapsed holder gets a full duration from now, not credit for the gap.
        assertEq(accessPass.expiresAt(buyer), block.timestamp + DURATION, "fresh expiry");
        assertTrue(accessPass.hasValidPass(buyer));
    }

    function test_onePassDoesNotGrantAccessToAnotherAddress() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        assertTrue(accessPass.hasValidPass(buyer));
        assertFalse(accessPass.hasValidPass(otherBuyer), "a pass is not bearer-shared on-chain");
    }

    function test_buyersHoldIndependentExpiries() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();
        uint256 buyerExpiry = accessPass.expiresAt(buyer);

        vm.warp(block.timestamp + 10 days);

        vm.prank(otherBuyer);
        accessPass.buyPass{value: PRICE}();

        assertEq(accessPass.expiresAt(buyer), buyerExpiry, "first buyer untouched");
        assertEq(accessPass.expiresAt(otherBuyer), block.timestamp + DURATION, "second buyer");
    }

    function test_theOwnerWithdrawsTheWholeBalance() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();
        vm.prank(otherBuyer);
        accessPass.buyPass{value: PRICE}();

        address payable treasury = payable(makeAddr("treasury"));

        vm.expectEmit(true, false, false, true, address(accessPass));
        emit AccessPass.Withdrawn(treasury, 2 * PRICE);

        vm.prank(owner);
        accessPass.withdraw(treasury);

        assertEq(treasury.balance, 2 * PRICE, "treasury balance");
        assertEq(address(accessPass).balance, 0, "contract drained");
    }

    function test_withdrawingDoesNotRevokeAnybodysPass() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();
        uint256 expiry = accessPass.expiresAt(buyer);

        vm.prank(owner);
        accessPass.withdraw(payable(makeAddr("treasury")));

        // Takings and entitlements are separate ledgers. Emptying one must not
        // touch the other.
        assertTrue(accessPass.hasValidPass(buyer));
        assertEq(accessPass.expiresAt(buyer), expiry, "expiry survives a withdrawal");
    }

    function test_aFreePassIsStillAPass() public {
        vm.prank(owner);
        AccessPass free = new AccessPass(0, DURATION);

        vm.prank(buyer);
        free.buyPass{value: 0}();

        assertTrue(free.hasValidPass(buyer));
        assertEq(free.expiresAt(buyer), block.timestamp + DURATION, "expiry");
    }
}
