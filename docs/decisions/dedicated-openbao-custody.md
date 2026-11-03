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
- Open: GitHub generates the app private key itself. Signing in transit needs the key imported,
  and this is not verified for OpenBao. If import is not possible, the fallback is a sealed key
  file readable only by the signer.

**Set aside.** The hub OpenBao with a new transit mount [^hub]. A sealed key file in the broker
process: the process, and anyone with host root, can read the key.

[^hub]: Reuses Kubernetes auth and policies, but shares the hub's root of trust, which already
    depends on a Cloudflare account that holds DNS, the tunnel and R2 state.
