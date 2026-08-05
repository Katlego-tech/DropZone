# DropZone

A time-limited access pass, bought on-chain, enforced off-chain.

## The problem this is really about

A blockchain can tell you that address `0xABC` holds a valid pass. It cannot tell your API
that the person making this HTTP request *is* `0xABC`.

That gap is the whole project. The payment is the boring half. A naive version of this takes
an address as a query parameter, asks the chain whether it holds a pass, and serves the
content when it does — at which point anyone who can read a block explorer can read your
protected data, because the caller never proved they control the key.

So this is an authentication problem that happens to be paid for in ETH. The protected
content is deliberately dull — a static dataset — so nothing distracts from the access
control around it.

## How it works

You buy a pass by sending ETH to a contract, which records that your address has access until
some future timestamp. Buying again while the pass is still live extends it rather than
resetting it, so renewing early costs you nothing.

Getting at the content then takes four steps:

1. Ask the API for a challenge — a one-time value it remembers for a few minutes.
2. Sign it with your wallet. The signature proves you hold the private key without revealing it.
3. The API recovers which address produced that signature and checks it matches the one you
   claimed. If it does, and that address holds a live pass, you get a short-lived session token.
4. Use the token to fetch the content.

Step 3 is the one that matters, and the one the naive version skips.

## Status

Iteration 0 — repo, build pipeline, and a health endpoint. Nothing is deployed and there's no
run command to give you yet.

I'm writing this README ahead of the code deliberately. The decisions below are what I want
argued with, and they're much cheaper to change now than after they're compiled. It'll grow as
the project does: the threat model, the architecture and the API reference belong here once
there's something real behind them, not before.

## What I'm deciding as I go

Each of these gets a proper write-up under [`docs/adr/`](docs/adr/) as it's decided.

### Python now, Java and Spring Boot later

I'm building the MVP in Python with FastAPI, and I intend to port the API to Java and Spring
Boot once I'm fluent in it.

This system has two unfamiliar halves: a domain I don't know yet (signatures, replay, chain
reorganisations) and a language I'm not yet fluent in. Taking both on at once means that when
something breaks I won't know which of my two unknowns is lying to me. Python is my strongest
language, so it's the one that gives the unfamiliar half my full attention.

What makes the port affordable is a constraint I'm taking on from the first commit: **the
acceptance tests are written against HTTP, not against Python.** They describe the flow in
requests and status codes and import nothing from the application, so they survive the rewrite
intact and become the specification the Java version has to satisfy. Learning a framework by
reimplementing a system whose correct behaviour is already defined beats learning it from a
blank file and a tutorial.

The honest risk is that this reads as ducking the harder language, and that a port I keep
postponing is a port that never happens. Full reasoning, including what triggers the port and
what I'll do if it slips, in [ADR 0001](docs/adr/0001-python-first-java-later.md).

### FastAPI, after seriously considering Django

I looked hard at Django first, specifically for its security defaults — this is an access
control system, so reaching for the framework that hardens the most by default seemed like the
responsible instinct.

Working through it changed my mind. Django's two headline protections don't apply here:
CSRF defends cookie-based auth, and mine is a bearer token that no browser attaches
automatically; the password and user framework doesn't describe an account that authenticates
by signature. What's left is parameterised SQL and some response headers, which any framework
is an import away from.

Meanwhile the parts that actually decide whether this system is secure — comparing the
recovered signer against the claimed address, consuming the nonce atomically, clamping session
expiry to pass expiry — ship with neither framework. So I chose on portability instead:
Django's ORM makes your table definition your domain object, which is the exact coupling the
Java port above depends on avoiding.

The cost is real. I lose Django's admin and its built-in rate limiting, and I'm now carrying an
explicit hardening checklist that Django would have let me not think about. Full reasoning in
[ADR 0002](docs/adr/0002-web-framework.md).

### Testing against a local chain, not just Sepolia

Testing everything against the public testnet I deploy to would mean one environment and no
fakes to maintain, which is tempting. It doesn't survive contact with this contract: the
behaviour that matters most is a pass *expiring*, and a local chain lets me jump the clock
forward a month between two assertions where a public one would have me wait.

The other reason is build trust. A suite that depends on a public network fails when the RPC
provider has a bad minute, and those failures look identical to real ones — a pipeline that
goes red because of the weather teaches you to ignore red pipelines.

So the contract is tested locally, the API is tested against a fake chain, and Sepolia is where
integration and the demo happen. CI never touches the network.
[ADR 0003](docs/adr/0003-where-tests-run.md).

### Still open

- **How many confirmations before access is granted.** A purchase can be undone by a chain
  reorganisation. Granting immediately is responsive and occasionally wrong; waiting is safe
  and feels broken to someone who just paid.
- **Asking the chain per request, or keeping my own copy.** One is simple and slow, the other
  is fast and eventually consistent. The usual cache-over-a-system-of-record trade.
- **How long a session should last.** Fewer wallet prompts against slower revocation.

None of these have a correct answer, only a defended one. That's why they're getting written
down rather than decided silently in a commit.

## What this can't do

**A pass holder can hand their session token to a friend, and nothing on-chain prevents it.**
Proving ownership of an address says who authenticated; it says nothing about who's holding
the browser afterwards. The sharing happens entirely off-chain, after the proof already
succeeded, so no amount of contract logic fixes it.

There are heuristics that make it harder — shorter sessions, device binding, watching for
concurrent use from different places — but they trade false positives against leakage and none
of them make the guarantee real. It's a limit of the model, not a defect in this build.

## Roadmap

Working software at the end of each iteration.

1. **Skeleton** — repo, CI running an empty test suite, container build, health endpoint.
   Prove the pipeline is green before there's anything to break.
2. **The contract** — buying a pass, expiry, renewal that extends rather than resets, and
   only the owner being able to withdraw. Tested against a local chain.
3. **The handshake** — challenge, signature verification and sessions, tested end to end
   against a fake chain so the suite needs no network.
4. **The real chain** — reading live contract state, and keeping a local projection of it in
   step with the network.
5. **Surface and ship** — a minimal frontend, deployment to a public testnet, and one command
   that runs the whole thing.

## Built with

Python and FastAPI for the API, Solidity for the contract, PostgreSQL for state, Docker for
delivery, deployed to the Sepolia testnet. Versions are pinned rather than floated.

## Sources

- [EIP-4361 — Sign-In With Ethereum](https://eips.ethereum.org/EIPS/eip-4361), the standard
  the sign-in flow implements
- [ethereum.org developer docs](https://ethereum.org/en/developers/docs/)
- [Solidity documentation](https://docs.soliditylang.org/) and
  [OpenZeppelin Contracts](https://github.com/OpenZeppelin/openzeppelin-contracts)
- [Consensys smart contract best practices](https://consensys.github.io/smart-contract-best-practices/)
