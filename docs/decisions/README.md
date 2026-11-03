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
| [capability-proxy](capability-proxy.md): runners get a capability, not a GitHub token | accepted |
| [mtls-runner-identity](mtls-runner-identity.md): runners authenticate with client certificates | accepted |
| [dedicated-openbao-custody](dedicated-openbao-custody.md): a dedicated OpenBao holds the app keys | proposed |
| [one-app-two-key-slots](one-app-two-key-slots.md): one GitHub App with two key slots | accepted |
| [license-agpl-commercial](license-agpl-commercial.md): AGPL-3.0 with a commercial license | accepted |
