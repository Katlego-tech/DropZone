# ADR 0003 — Where tests run: local chain, in-memory fake, and Sepolia

- **Status:** Accepted
- **Date:** 2026-08-05
- **Related:** ADR 0001 — Python now, Java and Spring Boot later

## Context

This system deploys to a public testnet, so the obvious simplification is to test against one
too: a single environment, no fakes to maintain, and maximum realism. Every test exercises the
thing that actually ships.

I want to write down why I'm not doing that, because "test against the real thing" is a
reasonable instinct and the reasons it fails here are specific rather than obvious.

## Why a public testnet can't be the only place tests run

**Time.** The central behaviour of this contract is expiry — a pass lapsing, and a renewal
extending an existing pass rather than resetting it. A local development node can jump its
clock forward thirty-one days between two assertions. A public chain cannot. Testing real
expiry on Sepolia means either waiting thirty-one days or deploying artificially short-lived
passes and sleeping in the test, which is slower *and* exercises a different configuration
from the one that ships.

**Build trust.** A suite that depends on a public network fails when the RPC provider has a
bad minute or the faucet balance runs dry. Those failures look exactly like real ones. A team
that sees red builds caused by weather learns to shrug at red builds, and then an actual
regression sails through unnoticed. A failing pipeline has to mean "I broke something" or it
means nothing at all.

**Determinism and speed.** Local nodes snapshot and revert between tests, so no state leaks
from one case into the next. Blocks are instant rather than roughly twelve seconds. Across a
few dozen contract tests that is the difference between a feedback loop and a break.

## Why local can't be the only place either

Local nodes are honest about the EVM and quietly dishonest about everything surrounding it.
The failures I expect in the indexer live precisely in that gap:

- RPC providers cap `eth_getLogs` block ranges and result sizes, forcing chunking and
  pagination that a local node never demands
- Transactions get dropped, replaced or stranded pending; gas estimation fails against real
  mempool conditions
- Nodes behind a load balancer disagree briefly about recent state, so a read immediately
  after a write can come back stale
- Real wallet behaviour — chain switching, user rejection, the actual prompts

None of that appears locally, and all of it is where "it works on my machine" comes from.

There is also a portfolio argument I'd rather state than pretend isn't a factor: a verified
contract with public transaction history can be checked by someone who has no reason to trust
me. A passing local test run cannot.

## Decision

Three layers, testing three different things.

| Layer | Runs against | Catches |
|---|---|---|
| Contract tests | Local node, with clock manipulation | Exact payment, expiry, renewal arithmetic, unauthorised withdrawal, reentrancy |
| API acceptance tests | `InMemoryChainGateway` — no chain at all | Signature recovery, nonce replay, session expiry clamping, the whole handshake |
| Integration and demo | Sepolia | RPC pagination, dropped transactions, stale reads, wallet flow, deployment |

**CI runs the first two layers and never touches the network.** The Sepolia layer is run
deliberately — before a deployment, and on a schedule — and is allowed to fail loudly without
blocking a merge, because its failures are frequently about the network rather than about the
code.

## Consequences

**The fake is now a liability I own.** An `InMemoryChainGateway` that drifts from the real
implementation makes a green suite meaningless, and this is the standard way a layered test
strategy rots. The mitigation is that both implementations are held to the same test suite:
the gateway contract tests run against the in-memory fake *and* against the RPC gateway
pointed at a local node. If the fake lies, that suite fails rather than the acceptance tests
silently passing against fiction.

**Reorg handling gets tested by simulation, not by observation.** Reorganisations deep enough
to matter are rare on Sepolia, so waiting to see one is not a strategy. The indexer's
reconciliation path is driven locally by rewinding the chain deliberately. This is the one
case where the local environment is *more* capable than the public one, not less — the
confirmation policy question is decided against a chain I can misbehave on demand.

**Faucet ETH becomes an operational dependency for demos and deployment, not for
development.** Nobody should need a funded account to run the test suite, which also means a
contributor can work offline and on a plane.

**Forking stays available as an escape hatch.** A local node seeded with real network state
gives testnet-realistic data at local speed with clock control. It is the answer if the fake
proves insufficient for some indexer case, and I'd rather reach for it than start running
acceptance tests over the network.

## What would change my mind

- **A bug reaching Sepolia that the fake should have caught.** That is evidence of drift, and
  the response is to strengthen the shared gateway suite — or, if the fake needs constant
  repair to stay honest, to delete it and fork instead. A fake that costs more than it saves
  should not survive out of loyalty.
- **Sepolia being deprecated.** Public testnets get sunset — Ropsten, Rinkeby and Goerli all
  went. That would change which network the third layer points at and nothing else about this
  decision, which is part of why the layering is worth having.
- **The indexer turning out to be where the real complexity lives.** If most defects are found
  in that layer rather than in the contract or the handshake, the balance of effort should
  shift towards forked-network testing, and this ADR should be revised to say so.
