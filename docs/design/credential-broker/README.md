# Credential broker (credential-broker): START HERE

A network service that issues short-lived, action-scoped credentials to external runners (agents
that bring their own models). It holds the long-lived keys in a dedicated OpenBao, checks who is
asking and for what, and delivers each credential in one of three modes: A, proxy it; B, hand over
a leased copy it can rotate and revoke; C, issue an OIDC identity token. It is additive to sandy's
`CredentialProvider` seam and does not replace it. It is NOT an LLM host, NOT a webhook-driven bot,
and NOT a general secret store.

## Read in this order

1. `credential-broker-proposal.md`: what and why.
2. `decision-log.md`: why it is this way, with the verified facts.
3. `credential-broker-spec.md`: how, as test-gated blocks.
4. `../../decisions/`: the active decision records.

## Status

Structurally complete, NOT finalized. Nothing is built. Open items to close before build:

- Block 1, the transit-signing acceptance test (the hinge below).
- Pilot values: capability TTL, client-certificate TTL, lease TTL, revoke-probe interval.
- opencode: whether its API key can carry the broker capability (mode A).
- Unseal path for the dedicated OpenBao.
- Certificate revocation: short lifetimes alone, or a CRL as well.

## The one decision the whole thing hinges on

Can the GitHub-generated app private key be imported into OpenBao transit and used to sign RS256
app JWTs that GitHub accepts? If yes, the key never leaves OpenBao. If no, the fallback is a
third-party OpenBao secrets plugin that holds the raw key, which is weaker and needs a source
review. This is open until Block 1 passes.

## Environments

- Pilot: a throwaway OpenBao (dev mode) plus a test GitHub App installed on one scratch repo in
  `kauki-labs`. Live adapter results count only from here, never from a fake.
- Load-sim: none. The broker's only timed behavior is expiry and rotation, measured in the pilot.
- Non-pilot: an in-memory fake upstream (GitHub, OpenRouter) covers policy, capability and lease
  logic without a live key.

## Repos referenced

- `kauki-labs/credential-broker` (this repo): follow `AGENTS.md`. Branch
  `<nick>/<type>/<component>/<desc>`, Conventional Commit titles, never push to `main`.
- `kauki-labs/infra`: the GitHub App registrations, install scope (`tf/cloud/02-github`), the
  dedicated OpenBao, and the broker's deployment.
- `kauki-labs/sandy`: the `CredentialProvider` seam the broker may later plug into
  (`docs/decisions/credential-seam.md`).
