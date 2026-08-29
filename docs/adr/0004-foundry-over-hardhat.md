# ADR 0004 — Foundry over Hardhat for the contract toolchain

- **Status:** Accepted
- **Date:** 2026-08-28
- **Related:** ADR 0001 — Python now, Java and Spring Boot later; ADR 0003 — Where tests run

## Context

The contract needs a toolchain: something to compile it, test it, and put it on a chain. The
two realistic choices are Hardhat and Foundry.

The build plan this project started from specified Hardhat, and its test names refer to
`evm_increaseTime`, which is a Hardhat helper. This ADR exists because I changed that decision
after ADR 0001 put the API in Python, and a decision reversed halfway through a plan deserves
a written reason more than one that was simply inherited.

## The argument that decided it

Hardhat's real advantage is not its test runner. It is that everything around the contract —
the deployment scripts, the fixtures, the frontend, the integration tests — is JavaScript, so
the contract work and the application work share one language, one package manager and one set
of libraries. On a project whose backend is Node, that is close to decisive.

**This project's backend is Python.** So the shared-language benefit evaporates, and what would
have been one ecosystem becomes two either way: Python plus JavaScript, or Python plus
Solidity. Given that I am paying the two-ecosystem cost regardless, the question becomes which
second ecosystem is the better one to pay for — and for testing a contract, it is Solidity.

## Why Solidity is the better language for these tests

**The test speaks the same language as the thing it tests.** A Hardhat test reaches the
contract through ethers.js, which means a JavaScript `BigInt` crossing an ABI boundary into a
`uint256`. Most of the time that is invisible. When it is not, the failure is in the
translation layer rather than in the contract, and I have spent debugging effort on my tooling
instead of on my code. A Foundry test calls `accessPass.buyPass{value: PRICE}()` and gets a
Solidity revert, typed and comparable by selector:

```solidity
vm.expectRevert(abi.encodeWithSelector(AccessPass.IncorrectPayment.selector, PRICE + 1, PRICE));
```

That asserts on the custom error *and its arguments*. The equivalent assertion in JavaScript is
string matching on a decoded revert reason, which passes for the wrong reasons more readily
than I would like.

**The attacker is a contract, so it should be written as one.** The reentrancy test needs a
hostile receiver whose `receive()` calls back into `withdraw`. That is not something a
JavaScript test can express — under Hardhat it would be a Solidity file compiled alongside the
tests and driven from JavaScript, which is the same Solidity plus a language boundary in the
middle of the most subtle test in the suite.

**Fuzzing is built in rather than bolted on.** `testFuzz_onlyTheExactPriceBuysAPass` and
`testFuzz_renewingNeverShortensAPass` are property assertions over 256 generated inputs each,
and cost one function signature. Getting that under Hardhat means adding a fuzzing tool and
learning its conventions. Two of the twenty-nine tests written for this iteration would not
have existed if they had been that much more expensive to write.

**Speed changes what gets tested.** The suite runs in about ten milliseconds. That is not
vanity: a suite that runs instantly gets run on every edit, and one that takes thirty seconds
gets run when you think you are finished. The four mutation tests that verified the suite
actually detects a broken contract — resetting renewal to `now`, accepting a minimum instead of
an exact payment, dropping `onlyOwner`, an off-by-one on the expiry — were only worth doing
because each round trip was seconds rather than minutes.

## What ADR 0003 asked for

ADR 0003 committed to clock manipulation as the mechanism for testing expiry, and named it as
the reason a public testnet cannot be the only place tests run. Both toolchains provide it —
`vm.warp` against Hardhat's `evm_increaseTime`. Foundry's version is a cheatcode callable
mid-test in the same language as the assertion around it, which makes the boundary test read
as a single thought:

```solidity
vm.warp(expiry - 1);
assertTrue(accessPass.hasValidPass(buyer), "one second before expiry");
vm.warp(expiry);
assertFalse(accessPass.hasValidPass(buyer), "at the expiry second");
```

This is a preference, not a capability gap. I am recording it as a preference.

## Decision

**Foundry**, pinned to v1.7.1, with forge-std v1.16.2 as a git submodule and `foundry.lock`
committed.

Foundry is rooted at the repository rather than at `contracts/`, because `forge install`
resolves to the git root and fighting that would have meant a second git repository or a
wrapper script. `foundry.toml` points `src`, `test`, `script` and `out` at `contracts/`, so the
Python package keeps `src/` and `tests/` and neither toolchain has to know about the other.

The compiler is pinned to 0.8.30 and `evm_version` to `shanghai` — the first so the bytecode
audited is the bytecode deployed, the second because nothing here needs a newer opcode and
pinning low keeps the same bytecode deployable on L2s whose EVM lags mainnet.

## Consequences

**The build plan's `evm_increaseTime` tests are now `vm.warp` tests.** The behaviours it named
are all covered; the helper it named is not the one used. Anyone reading the plan alongside the
repository should treat this ADR as the correction.

**Solidity is now a language I have to be competent in for tests, not just for the contract.**
That is a real cost and I am counting it as a benefit: the tests are the part of this
repository most likely to be read closely, and writing them in Solidity means more practice
with the language the project is actually about.

**The frontend in iteration 4 will need Node regardless.** Foundry does not remove JavaScript
from the project, it removes it from the contract's test loop. `node_modules/` stays in
`.gitignore` for that reason.

**Deployment is a Solidity script rather than a JavaScript one.**
`contracts/script/Deploy.s.sol` reads its terms from the environment and takes its signer from
forge's own flags, so no code path in the repository can read or print a private key. The
tradeoff is that anything a deployment needs beyond a constructor call — reading a JSON
manifest, calling an external API, writing a config file for the API — is awkward in Solidity
in a way it would not be in JavaScript. If deployment grows that kind of logic, it belongs in a
Python script driving `forge` rather than in a larger `.s.sol`.

**Fewer people can review this than could review a Hardhat project.** Hardhat is the more
widely known toolchain, and choosing the less common one narrows the pool of people who can
read the repository without first reading Foundry's documentation. That is a genuine cost of
this decision, not a rounding error, and it is why this ADR is longer than it strictly needs
to be.

## What would change my mind

- **The frontend and the contract wanting to share code.** If iteration 4 produces typed
  contract bindings, ABIs and address constants that both a JavaScript frontend and the
  deployment path need, the single-ecosystem argument for Hardhat comes back with real weight.
  Foundry can emit ABIs for a frontend to consume, so this would have to be more than ABI
  sharing to move me.
- **Deployment logic outgrowing a Solidity script**, as above — though the first response is a
  Python driver, not a change of toolchain.
- **Needing a plugin that only exists for Hardhat.** Upgradeability proxies and some
  verification workflows have better-trodden Hardhat paths. Neither is in scope: this contract
  is immutable by design, which is most of why the question does not arise.
- **A team joining who know Hardhat and not Foundry.** Toolchain decisions are about the
  people using them, and this one was made for a repository with one author.
