---
title: The broker lives in its own repo
status: accepted
project: credential-broker
updated: 2026-11-03
---

# The broker lives in its own repo

**Context.** The broker guards keys that reach every installation of the GitHub App. Sandy
already has a credential seam (`CredentialProvider`) and a GitHub installation-token minter,
but it hands a real token to the guest and shares a public repo with outside contributors.

**Decision.** The broker is its own repo, `kauki-labs/credential-broker`. Collaborators, branch
protection, Actions secrets and release rights are independent of sandy. Sandy may later call
the broker as a remote provider behind its existing seam. The broker takes no code from sandy.

**Consequences.**
- One more repo to run: its own CI and release flow.
- GitHub JWT and installation-token minting may exist twice. Extract a shared crate only when a
  third consumer appears.
- Access control for key-handling code is plain repo access, not CODEOWNERS paths in a shared
  repo.

**Set aside.** Crates inside the sandy workspace [^sandy]: little real code is shared, and the
signing path would sit in a repo with guest-facing code and outside contributors.

[^sandy]: Considered with guardrails (a core crate with no sandyd dependency, CODEOWNERS on the
    key-handling paths). Dropped because the guardrails only approximate the isolation a separate
    repo gives for free.
