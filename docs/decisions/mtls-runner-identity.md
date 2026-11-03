---
title: Runners authenticate with client certificates
status: accepted
project: credential-broker
updated: 2026-11-03
---

# Runners authenticate with client certificates

**Context.** Runners run outside the broker's trust domain: a separate Kubernetes cluster or a
sandy microVM. A bearer token is copyable and works anywhere until rotated.

**Decision.** Runners authenticate to the broker with mTLS client certificates. The certificate
identity selects the runner's policy: allowed actions, repositories and maximum TTL. Capabilities
are bound to that identity, so a stolen capability does not work from another runner.

**Consequences.**
- A small CA and certificate rotation to run. The dedicated OpenBao PKI engine is the intended
  CA (see [dedicated-openbao-custody](dedicated-openbao-custody.md)).
- Works for microVMs outside Kubernetes.
- Revocation needs short certificate lifetimes or a CRL. The spec must pick one.

**Set aside.** Kubernetes workload OIDC [^oidc]: no CA to run, but it ties runners to one
cluster. Per-client bearer tokens: the clients-file pattern in `sandy-broker`, weaker against
theft.

[^oidc]: Was the first recommendation. Dropped because runners may be microVMs with no
    Kubernetes service account.
