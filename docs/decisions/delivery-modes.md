---
title: Each credential type gets a delivery mode
status: proposed
project: credential-broker
updated: 2026-11-03
supersedes: capability-proxy
---

# Each credential type gets a delivery mode

**Context.** The first design always proxied. A GitHub installation token is scoped by repository
set and permission set and lives one hour; GitHub has no token scope below that. But some clients
read a token themselves, some upstreams issue keys on request (OpenRouter), and some may later
trust the broker as an identity issuer. One delivery mode does not fit all of them.

**Decision.** The broker core (runner identity, policy, signing through OpenBao, audit) serves
three modes, chosen per credential type and harness in policy:
- **A, proxy.** The runner holds a capability, bound to its identity, an action, a target and an
  expiry. The broker keeps the real credential, checks each request against the capability,
  injects the credential and forwards. Default for GitHub actions and for Anthropic direct.
- **B, leased handover.** The runner holds a real short-lived credential. The broker records it as
  a lease in OpenBao and revokes it at expiry, teardown or anomaly, then probes for a 401. Default
  for OpenRouter keys and for clients that need a real GitHub token.
- **C, identity federation.** The runner holds an OIDC identity token the upstream verifies. A
  seam only until an upstream trusts the broker as an issuer.

Rotation and revocation are on by default in A and B: held credentials are re-minted before
expiry, mode A proxies are ephemeral per job, TLS identities come from the OpenBao PKI engine with
a short TTL, and capabilities are short-lived. If an upstream revoke fails, the broker denies new
leases to that runner, retries and alerts.

**Consequences.**
- A leaked runner credential reaches one action on one target in A, or one capped key in B, until
  it expires or is revoked.
- Mode A makes the broker a hot path that must speak each upstream protocol. GraphQL stays denied
  until an action needs it.
- Mode B puts a usable secret in the runner, so it needs the strongest revoke path.
- A held key with no create API (Anthropic direct) and the GitHub app key rotate by hand.
- A new mode or upstream is added without changing identity, policy or audit.

**Set aside.** Always proxy [^proxy]; native tokens only [^native].

[^proxy]: Some clients cannot be pointed at a proxy, and OpenRouter issues per-run keys with spend
    caps that are safer to hand over than to funnel through a custom proxy.

[^native]: Stops at repository and permission scope. Kept as the model for the token the broker
    mints internally, which is always narrowed by repository and permission.
