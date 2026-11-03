---
title: Runners bring their own models
status: accepted
project: credential-broker
updated: 2026-11-03
---

# Runners bring their own models

**Context.** The first idea was a GitHub App that calls LLM models. That puts model cost,
provider keys and prompt-injection exposure inside a service that also holds GitHub keys.

**Decision.** The broker calls no LLM. Whoever runs a model (a person, a cluster, a sandy
microVM) runs it on their side and asks the broker only for GitHub credentials. The broker is
a credential service, not an agent.

**Consequences.**
- No model cost or provider key custody in the broker.
- Untrusted issue and PR text reaches the runner's model, never the broker. A steered runner
  is still bounded by the capability it holds.
- The broker cannot act on webhooks by itself. Any trigger that starts a runner is a separate
  component.

**Set aside.** A broker that hosts the model [^host]: it joins two high-value secrets (LLM keys
and GitHub keys) and the injection surface in one process.

[^host]: Rejected on blast radius. Revisit only if a hosted-model product is wanted.
