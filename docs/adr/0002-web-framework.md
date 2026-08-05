# ADR 0002 — FastAPI over Django for the API layer

- **Status:** Accepted
- **Date:** 2026-08-05
- **Related:** ADR 0001 — Python now, Java and Spring Boot later

## Context

The API is being built in Python (ADR 0001). The two realistic choices are Django with Django
REST Framework, and FastAPI.

I was pulled towards Django specifically for its security posture. It ships hardening that
most frameworks make you assemble yourself, and this project is an access-control system, so
"pick the framework with the best security defaults" felt like the responsible instinct.

This ADR exists because I wanted to test that instinct rather than follow it.

## What this application actually is

A stateless JSON API. Authentication is an ECDSA signature over a structured message
(EIP-4361). A session is a bearer JWT. There is no HTML rendering, no cookie session, and no
password anywhere in the system.

That paragraph turns out to decide most of the question.

## Django's security features against this threat model

| Feature | Applies here? |
|---|---|
| CSRF middleware | **No.** CSRF exists because browsers attach cookies to requests automatically. A token in an `Authorization` header is attached by my own client code, so there is no ambient authority to forge. |
| Template auto-escaping | **No.** No templates. |
| `contrib.auth`, PBKDF2 password hashing | **No.** No passwords. The user is an address that authenticates by signature; the `User` model doesn't describe it and the login flow is one I write regardless. |
| Session framework, secure cookie handling | **No.** Sessions are JWTs, deliberately, so the content path can be verified locally without a lookup. |
| ORM parameterisation | **Yes** — and SQLAlchemy parameterises identically. Not a differentiator. |
| Security headers (HSTS, nosniff, X-Frame-Options) | **Yes**, and it is one middleware in either framework. Mostly matters at the frontend origin, not the API. |

The two headline features — CSRF protection and the auth framework — are neutralised by the
authentication model, not by anything I've chosen to skip. What survives is parameterised SQL
and a handful of response headers, both roughly one import away in anything.

## Where the risk actually lives

The security-critical decisions in this system are:

- recovering the signer from the signature and asserting it equals the **claimed** address;
- consuming the nonce **atomically**, so a check-then-consume race can't become a replay;
- clamping the session expiry to the pass expiry, so a session can't outlive what paid for it;
- binding domain and chain id into the signed message, so a signature harvested elsewhere
  doesn't work here;
- deciding how many confirmations a purchase needs before it grants access;
- validating the JWT correctly, including pinning the algorithm rather than trusting the one
  named in the token header.

Neither framework ships any of that. Choosing on framework security defaults optimises a
variable that is nearly constant across the options, while all the variance sits in code I
write either way. The choice has to be made on something else.

## The something else: the port to Spring Boot

ADR 0001 commits to keeping domain logic free of framework types, because that is what makes
the eventual Java port layer-by-layer instead of a rewrite. That commitment discriminates
between these two frameworks sharply.

Django's ORM is **ActiveRecord**. A `models.Model` is the table definition, the validation
rules and — in practice, and in every idiom the ecosystem teaches — the domain object too.
Keeping business logic out of Django models is possible, but it means working against the
grain of the framework continuously, and the pressure is strongest exactly when I'm tired and
shipping.

SQLAlchemy is **Data Mapper**. Domain objects are plain classes; the mapping to tables is
declared separately. The separation ADR 0001 depends on is the default rather than a
discipline I have to sustain.

The counter-argument I have to concede: Spring Boot is conceptually much closer to Django than
to FastAPI — batteries included, dependency injection, ORM-centric, convention over
configuration. Django models map onto JPA entities almost one to one. But JPA entities have
precisely the same appetite for swallowing the domain that Django models do, so that port
would carry the coupling across intact rather than resolve it. The mapping would be more
familiar; it would not be cleaner.

## Decision

**FastAPI with SQLAlchemy.**

A secondary reason, in FastAPI's favour rather than Django's disfavour: request bodies are
validated against declared schemas at the boundary before any handler sees them, and the
OpenAPI description is generated from those same declarations. Input validation is a security
property, and having it be structural rather than remembered is worth more here than most of
what Django would have contributed.

## What I lose, and what I now have to do explicitly

**Django admin.** A genuine loss. It would have given me a free operational surface for
inspecting passes, nonces and the indexer's block cursor. I'm accepting `psql` and, if that
becomes painful, a small read-only internal endpoint.

**DRF throttling.** This is the one absence that creates real work, and it matters:
`/auth/challenge` is unauthenticated and writes a row per call, which is both a denial-of-
service vector and storage amplification. It gets rate limited explicitly rather than left
implicit.

The rest of the hardening Django would have defaulted into, I now own as a checklist:

- [ ] Rate limit `/auth/challenge` per address and per IP
- [ ] Allowed-hosts check and an explicit CORS origin allowlist
- [ ] Security response headers (HSTS, nosniff, frame-deny)
- [ ] JWT: algorithm pinned at decode time, never read from the token header; secret from the
      environment with a documented rotation story
- [ ] TLS terminated in front of the application, HTTP redirected

Writing that list is the actual output of this decision. It would have existed either way; the
difference is that Django would have let me not think about it.

## What would change my mind

- The frontend becoming server-rendered rather than a separate client. Template escaping and
  CSRF become load-bearing the moment there are cookies and HTML, and Django wins outright.
- A real user model appearing over the top of addresses — email, roles, team membership. That
  is what `contrib.auth` is for, and rebuilding it would be a bad use of my time.
- Operations growing to the point where a back office is a weekly need rather than an
  occasional one. The admin would then outweigh the ORM cost.
