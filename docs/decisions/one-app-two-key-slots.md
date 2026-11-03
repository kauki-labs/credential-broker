---
title: One GitHub App with two key slots
status: accepted
project: credential-broker
updated: 2026-11-03
---

# One GitHub App with two key slots

**Context.** The goal is read and write separation on keys. A GitHub App's permission ceiling
belongs to the app, not to a key. Two keys on one app can both mint tokens up to the app's full
ceiling.

**Decision.** One app per owner, installed only on the maintainer's own accounts. A private app
installs only on its owner, so the personal account and `kauki-labs` each get one (as sandy-runner
does). Each app holds two key slots, one used by a read-path signer and one by a write-path signer.
The broker enforces read and write separation in policy: it asks GitHub for a token narrowed to
the requested permissions each time.

**Consequences.**
- A leak of either key reaches the full write ceiling of the app on every installation. Key
  slots give rotation and per-signer revocation, not privilege separation.
- Mitigate by keeping the ceiling minimal: no `workflows`, `administration` or org-level
  permissions, and `contents: write` only while a write action needs it.
- A signer that cannot reach the write key still needs a separate process and OpenBao policy for
  the separation to mean anything in the broker.

**Set aside.** Two apps, one read-only and one with write [^two]: the only option where a read
key leak cannot push code.

[^two]: Recommended during design and the upgrade path. Costs a second registration and a second
    install per owner. Revisit before the app is installed anywhere it matters.
