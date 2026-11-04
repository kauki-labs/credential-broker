---
title: Sign GitHub app JWTs with OpenBao transit
status: proposed
project: credential-broker
updated: 2026-11-03
---

# Sign GitHub app JWTs with OpenBao transit

**Context.** The GitHub App private key signs the JWT that mints tokens for every installation.
GitHub generates the key and hands it out as a PEM. OpenBao transit signs RS256 with `pkcs1v15`
and `sha2-256`, and can import external RSA keys, wrapped with its wrapping key.

**Decision.** The key is imported into a dedicated OpenBao transit mount as non-exportable with
`allow_rotation=false`, and the broker signs through the API. The key never sits in the broker or
a runner. This is accepted only after an acceptance test (spec Block 1) passes against a test
installation. If import or GitHub acceptance fails, the fallback is a third-party OpenBao GitHub
plugin, after a source review.

**Consequences.**
- The key passes through the operator's machine once, at import. Convert and import it in one
  sitting and delete the plaintext afterwards.
- GitHub app key rotation stays manual: generate a new key in GitHub, import it as a new slot,
  retire the old one.
- The plugin fallback holds the raw key to sign, so it is weaker and needs its own scrutiny.

**Set aside.** The community plugin as the default [^plugin]; a sealed key file readable by the
broker [^file].

[^plugin]: It needs the raw PEM to sign, where transit signs without exposing it. It is a young
    third-party fork (v0.1.5 at the time of writing).

[^file]: The broker process, and anyone with host root, can read the key.
