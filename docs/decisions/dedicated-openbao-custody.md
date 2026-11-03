---
title: A dedicated OpenBao holds the app keys
status: proposed
project: credential-broker
updated: 2026-11-03
---

# A dedicated OpenBao holds the app keys

**Context.** The GitHub App private key signs a JWT that mints tokens for every installation.
The key must not sit in the broker process or on a runner. The hub OpenBao unseals through a
Cloudflare Worker and serves other tenants. The vault VM (`.54`) is the hub's root of trust and
runs only the unseal key.

**Decision.** A dedicated OpenBao instance holds the app key in a transit engine, and the
broker signs through its API. The same instance may also run the PKI engine for the runner CA.
Mounts and policies stay separate per function. The instance never unseals the hub.

**Consequences.**
- Its own trust domain: a hub compromise does not reach the app keys.
- Another service to run, back up and unseal. Its unseal path is undecided.
- Each added function widens what a compromise of this instance reaches. Keep it to signing and
  machine identity.
- Signing in transit needs GitHub's generated key imported. OpenBao supports RSA key import; the
  acceptance test is in [transit-signing-first](transit-signing-first.md).
- The instance does signing and machine identity only: transit, PKI and lease records. A mount
  for another purpose needs its own decision.

**Set aside.** The hub OpenBao with a new transit mount [^hub]. A sealed key file in the broker
process: the process, and anyone with host root, can read the key. Its unseal path is undecided.

[^hub]: Reuses Kubernetes auth and policies, but shares the hub's root of trust, which already
    depends on a Cloudflare account that holds DNS, the tunnel and R2 state.
