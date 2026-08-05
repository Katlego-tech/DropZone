# ADR 0001 — Python now, Java and Spring Boot later

- **Status:** Accepted
- **Date:** 2026-08-05
- **Related:** ADR 0002 — FastAPI over Django for the API layer

## Context

This system has two independent sources of difficulty. The domain is unfamiliar: signature
recovery, replay defence, chain reorganisations, confirmation policy. And the language I want
to end up working in professionally is Java with Spring Boot, which I am not yet fluent in.

Taking both on at once is the obvious plan and I think it is the wrong one. When something
fails at three in the morning, I would not know whether I had misunderstood EIP-4361 or
misconfigured a bean, and the debugging loop for "which of my two unknowns is lying to me" is
brutally slow. It is also the fastest route to having nothing that works at the end of the
first month, which defeats the point of iterating at all.

Python is my strongest language. That makes it the one in which the unfamiliar half of this
problem gets my full attention.

## Options considered

**Java and Spring Boot from the start.** Most direct, and no port to schedule. Rejected: it
learns the framework and the domain simultaneously, and the domain is the part worth
understanding properly. The framework can wait until the behaviour is settled.

**Python, permanently.** Honest, and I considered it seriously rather than as a straw man. It
would produce a finished system sooner and with less ceremony. Rejected because the
professional goal is real, not decorative — I want Spring Boot fluency, and this is a system
whose behaviour I will understand well enough to rebuild.

**Python now, port to Java and Spring Boot once the behaviour is pinned down by tests.**
Chosen.

## Decision

Build the MVP in Python with FastAPI. Once the acceptance suite is green and the behaviour has
stopped moving, port the API to Java and Spring Boot, using that suite as the specification the
Java version has to satisfy.

Learning a framework by reimplementing a system whose correct behaviour is already defined is a
substantially better exercise than learning it from a blank file and a tutorial. Every failure
during the port is a fact about Spring Boot, because the requirements are no longer in question.

## What makes the port affordable

One constraint, adopted from the first commit rather than retrofitted:

**The acceptance tests are written against HTTP, not against Python.** They describe the flow
in terms of requests, headers and status codes. Nothing in them imports application code. They
therefore survive the rewrite untouched and become the contract the Java implementation has to
meet — which also means the port has a definition of done that isn't a feeling.

Supporting that, the layered split: a domain layer that knows nothing about the web framework,
the database or the chain, with the awkward external dependencies behind interfaces I own.
That makes the port layer-by-layer rather than all-at-once, and it means the domain — the part
carrying the actual thinking — is the part that moves most cleanly.

## Consequences

**The discipline this costs me.** No framework types in the domain layer. No database models
used as domain objects. Translation at the edges rather than passing a validated request body
straight through to business logic. In Python all of that reads as unnecessary boilerplate,
and it will keep reading that way right up until the port, which is the point at which it pays
for itself all at once. This is the constraint most likely to erode quietly under deadline
pressure, and the one worth defending hardest. ADR 0002 chose the ORM on exactly these grounds.

**The risk I am naming rather than hiding.** This can read as ducking the harder language, and
a port that keeps being postponed is a port that never happens. I would rather write that down
than have someone notice it for me.

**The mitigation.** The port is not "eventually" — it is triggered when the real-chain
iteration lands with the acceptance suite green and the behaviour stable. If it slips more than
two iterations past that, the honest move is to say so here and either commit to a date or
change the status of this ADR to reflect that Python is now the permanent home. An aspirational
plan left standing in a repository is worse than one that was revised out loud.

**Dual toolchain during the transition.** Both stacks exist and both must stay green until the
Java version passes the full suite and the Python one is deleted. Deleted, not left beside it —
two implementations of the same API is not a hedge, it is two things to maintain and one of
them will rot.

## What would change my mind

- **The acceptance tests turn out to be coupled to Python.** That would be a design failure
  rather than a language problem, and the fix would be to repair the tests, not to abandon the
  port.
- **The port keeps slipping.** Covered above: revise this ADR rather than let it stand as
  decoration.
- **Something about the runtime makes Python the right permanent home** — a genuine technical
  reason, discovered rather than reached for. I want to be honest with myself about the
  difference, because the comfortable answer and the correct one point the same way here, and
  that is precisely when to be suspicious.
