// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Test} from "forge-std/Test.sol";

import {AccessPass} from "../src/AccessPass.sol";

/// @notice An owner whose withdrawal address is a contract that tries to be paid
///         twice. `withdraw` hands control to `to` mid-call, which is the only
///         reentrancy surface this contract has; this is what sits on it.
/// @dev It deploys the AccessPass itself so that it *is* the owner. A hostile
///      receiver that is not the owner never gets that far — `onlyOwner` stops it
///      at the door — so making the attacker the owner is the harder case.
contract ReentrantOwner {
    AccessPass public immutable accessPass;

    uint256 public reentryAttempts;
    bool public reentrySucceeded;

    constructor(uint256 price, uint256 duration) {
        accessPass = new AccessPass(price, duration);
    }

    function drain() external {
        accessPass.withdraw(payable(address(this)));
    }

    receive() external payable {
        // Guard the recursion, not the attack: one re-entry is enough to prove the
        // point, and an unbounded one would only ever run out of gas.
        if (reentryAttempts != 0) return;
        reentryAttempts = 1;

        // A low-level call so a revert inside it is a return value rather than a
        // revert of the outer withdrawal. Swallowing it is what a real attacker
        // would do; it keeps the failure from being mistaken for the defence.
        (bool ok,) =
            address(accessPass).call(abi.encodeCall(AccessPass.withdraw, (payable(address(this)))));
        reentrySucceeded = ok;
    }
}

/// @notice A withdrawal target that cannot accept ETH at all.
contract RefusesEther {
    // No receive, no fallback, on purpose.
}

/// @title AccessPass — the paths that are meant to fail
/// @notice Read the test names as the attacks this contract defeats. Anyone can
///         demonstrate a purchase; this file is the part worth rehearsing.
contract AccessPassAttackTest is Test {
    AccessPass internal accessPass;

    uint256 internal constant PRICE = 0.01 ether;
    uint256 internal constant DURATION = 30 days;

    address internal owner;
    address internal buyer;
    address internal stranger;

    function setUp() public {
        owner = makeAddr("owner");
        buyer = makeAddr("buyer");
        stranger = makeAddr("stranger");

        vm.warp(1_767_225_600); // 2026-01-01T00:00:00Z

        vm.prank(owner);
        accessPass = new AccessPass(PRICE, DURATION);

        vm.deal(buyer, 10 ether);
        vm.deal(stranger, 10 ether);
    }

    // --- Paying the wrong amount -------------------------------------------

    function test_underpayingBuysNothing() public {
        vm.prank(buyer);
        vm.expectRevert(
            abi.encodeWithSelector(AccessPass.IncorrectPayment.selector, PRICE - 1, PRICE)
        );
        accessPass.buyPass{value: PRICE - 1}();

        assertFalse(accessPass.hasValidPass(buyer), "no pass");
        assertEq(address(accessPass).balance, 0, "no takings");
    }

    function test_overpayingIsRejectedRatherThanRefunded() public {
        // The refund a lenient contract would owe here is an external call to an
        // address the caller chose. Refusing the payment deletes that path.
        vm.prank(buyer);
        vm.expectRevert(
            abi.encodeWithSelector(AccessPass.IncorrectPayment.selector, PRICE + 1, PRICE)
        );
        accessPass.buyPass{value: PRICE + 1}();

        assertFalse(accessPass.hasValidPass(buyer), "no pass");
    }

    function test_payingNothingBuysNothing() public {
        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(AccessPass.IncorrectPayment.selector, 0, PRICE));
        accessPass.buyPass();

        assertFalse(accessPass.hasValidPass(buyer), "no pass");
    }

    function testFuzz_onlyTheExactPriceBuysAPass(uint256 amount) public {
        vm.assume(amount != PRICE);
        amount = bound(amount, 0, 100 ether);
        vm.assume(amount != PRICE);

        vm.deal(buyer, 200 ether);
        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(AccessPass.IncorrectPayment.selector, amount, PRICE));
        accessPass.buyPass{value: amount}();

        assertFalse(accessPass.hasValidPass(buyer), "no pass");
    }

    // --- Terms that cannot be set ------------------------------------------

    function test_aZeroDurationPassCannotBeDeployed() public {
        // It would mint passes that expired in the same block they were bought.
        vm.expectRevert(AccessPass.ZeroDuration.selector);
        new AccessPass(PRICE, 0);
    }

    function test_thePriceCannotBeChangedAfterDeployment() public {
        // There is no setter to call. Asserting on the selector keeps this test
        // honest if somebody later adds one.
        (bool ok,) = address(accessPass).call(abi.encodeWithSignature("setPrice(uint256)", 1 wei));
        assertFalse(ok, "no setter exists");
        assertEq(accessPass.price(), PRICE, "price unchanged");
    }

    // --- Draining the contract ---------------------------------------------

    function test_aStrangerCannotWithdraw() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        vm.prank(stranger);
        vm.expectRevert(AccessPass.NotOwner.selector);
        accessPass.withdraw(payable(stranger));

        assertEq(address(accessPass).balance, PRICE, "takings intact");
    }

    function test_aPassHolderCannotWithdraw() public {
        // Paying for access buys access. It does not buy any authority over the
        // contract's balance.
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        vm.prank(buyer);
        vm.expectRevert(AccessPass.NotOwner.selector);
        accessPass.withdraw(payable(buyer));
    }

    function test_theOwnerCannotBurnTheTakings() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        vm.prank(owner);
        vm.expectRevert(AccessPass.ZeroAddress.selector);
        accessPass.withdraw(payable(address(0)));
    }

    function test_withdrawingAnEmptyBalanceReverts() public {
        vm.prank(owner);
        vm.expectRevert(AccessPass.NothingToWithdraw.selector);
        accessPass.withdraw(payable(owner));
    }

    function test_aWithdrawalToAnAddressThatRefusesEtherRevertsWholesale() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        address payable refuser = payable(address(new RefusesEther()));

        vm.prank(owner);
        vm.expectRevert(AccessPass.TransferFailed.selector);
        accessPass.withdraw(refuser);

        // The revert unwinds the whole call, so the takings are still here rather
        // than recorded as sent.
        assertEq(address(accessPass).balance, PRICE, "takings intact");
    }

    function test_aHostileOwnerCannotBePaidTwiceByReentering() public {
        ReentrantOwner attacker = new ReentrantOwner(PRICE, DURATION);
        AccessPass target = attacker.accessPass();

        vm.prank(buyer);
        target.buyPass{value: PRICE}();
        vm.prank(stranger);
        target.buyPass{value: PRICE}();
        assertEq(address(target).balance, 2 * PRICE, "takings before the attack");

        attacker.drain();

        assertEq(attacker.reentryAttempts(), 1, "the attack actually ran");
        assertFalse(attacker.reentrySucceeded(), "the re-entrant withdrawal failed");

        // Paid exactly once. The balance is sent before `to`'s code runs, so the
        // re-entrant call finds nothing to withdraw even before CEI is considered.
        assertEq(address(attacker).balance, 2 * PRICE, "attacker paid once");
        assertEq(address(target).balance, 0, "nothing left behind");
    }

    // --- Money arriving by the wrong door -----------------------------------

    function test_etherSentDirectlyIsBounced() public {
        vm.deal(address(this), 1 ether);

        // No receive, no fallback: ETH arrives through buyPass or not at all.
        // Keeping money that bought nothing would be worse than refusing it.
        (bool ok,) = address(accessPass).call{value: 1 ether}("");

        assertFalse(ok, "plain transfer rejected");
        assertEq(address(accessPass).balance, 0, "no takings");
    }

    function test_callingAnUnknownFunctionReverts() public {
        (bool ok,) =
            address(accessPass).call(abi.encodeWithSignature("mintFreePass(address)", buyer));

        assertFalse(ok, "unknown selector rejected");
        assertFalse(accessPass.hasValidPass(buyer), "no pass");
    }

    // --- Time ---------------------------------------------------------------

    function test_anExpiredPassDoesNotComeBack() public {
        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        vm.warp(accessPass.expiresAt(buyer) + 365 days);

        assertFalse(accessPass.hasValidPass(buyer), "still expired a year later");
        assertGt(accessPass.expiresAt(buyer), 0, "the record is kept, the access is not");
    }

    function testFuzz_renewingNeverShortensAPass(uint256 elapsed) public {
        elapsed = bound(elapsed, 0, 10 * DURATION);

        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();
        uint256 before = accessPass.expiresAt(buyer);

        vm.warp(block.timestamp + elapsed);

        vm.prank(buyer);
        accessPass.buyPass{value: PRICE}();

        // Whenever a holder renews, they end up with more time than they had, and
        // never less. A reset-to-now implementation fails this.
        assertGe(accessPass.expiresAt(buyer), before, "renewal never loses time");
        assertGt(accessPass.expiresAt(buyer), block.timestamp, "and always leaves a live pass");
    }
}
