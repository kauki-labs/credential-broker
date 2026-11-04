---
title: credential-broker decisions
kind: decisions-index
project: credential-broker
updated: 2026-11-03
---

# Decisions

One record per active decision. A denied alternative is a footnote on the decision it bears on.

| Decision | Status |
|----------|--------|
| [standalone-repo](standalone-repo.md): the broker lives in its own repo | accepted |
| [broker-hosts-no-llm](broker-hosts-no-llm.md): runners bring their own models | accepted |
| [delivery-modes](delivery-modes.md): each credential type gets a delivery mode (proxy, leased handover, OIDC); supersedes capability-proxy | proposed |
| [transit-signing-first](transit-signing-first.md): sign GitHub app JWTs with OpenBao transit, plugin as fallback | proposed |
| [mtls-runner-identity](mtls-runner-identity.md): runners authenticate with client certificates | accepted |
| [dedicated-openbao-custody](dedicated-openbao-custody.md): a dedicated OpenBao holds the app keys | proposed |
| [one-app-two-key-slots](one-app-two-key-slots.md): one GitHub App with two key slots | accepted |
| [installer-owned-apps](installer-owned-apps.md): each installer registers and owns their own App | accepted |
| [license-agpl-commercial](license-agpl-commercial.md): AGPL-3.0 with a commercial license | accepted |
