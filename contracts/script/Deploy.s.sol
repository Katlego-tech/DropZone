// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";

import {AccessPass} from "../src/AccessPass.sol";

/// @title Deploy AccessPass
/// @notice One script for every network. The only thing that changes between a
///         local anvil run and a testnet deployment is the RPC URL and the signer,
///         both of which forge supplies on the command line — so the deployment
///         that ships is the deployment that was rehearsed.
///
/// Local:
///   anvil
///   forge script contracts/script/Deploy.s.sol \
///     --rpc-url http://127.0.0.1:8545 --broadcast \
///     --private-key <one of anvil's printed keys>
///
/// Anywhere else, substitute the RPC URL and use --account with a keystore rather
/// than --private-key, so the key is never in a shell history or an environment.
///
/// @dev The signer is deliberately not read in here. vm.startBroadcast() takes it
///      from forge's own flags, which means this file has no code path that could
///      ever print or leak a private key, and no reviewer has to check that it
///      doesn't.
contract DeployAccessPass is Script {
    /// @dev Defaults are the terms in the README. They exist so that a local run
    ///      needs no environment at all; every real deployment should set both
    ///      explicitly, because the terms are immutable once deployed.
    uint256 internal constant DEFAULT_PRICE_WEI = 0.01 ether;
    uint256 internal constant DEFAULT_DURATION_SECONDS = 30 days;

    function run() external returns (AccessPass accessPass) {
        uint256 price = vm.envOr("ACCESS_PASS_PRICE_WEI", DEFAULT_PRICE_WEI);
        uint256 duration = vm.envOr("ACCESS_PASS_DURATION_SECONDS", DEFAULT_DURATION_SECONDS);

        // Fail before broadcasting rather than after. The constructor rejects this
        // too, but finding out here costs nothing, while finding out on-chain costs
        // a reverted transaction and its gas.
        require(duration != 0, "ACCESS_PASS_DURATION_SECONDS must not be zero");

        console2.log("price (wei)     ", price);
        console2.log("duration (secs) ", duration);

        vm.startBroadcast();
        accessPass = new AccessPass(price, duration);
        vm.stopBroadcast();

        console2.log("AccessPass      ", address(accessPass));
        console2.log("owner           ", accessPass.owner());

        // The API needs this address and this block. Reading it out of the broadcast
        // JSON is possible but tedious; printing it is what somebody actually does.
        console2.log("deployed at block", block.number);
    }
}
