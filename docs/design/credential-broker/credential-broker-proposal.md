# Credential broker: proposal

## 1. Purpose and non-goals

Give runners that sit outside the broker's trust domain narrow, short-lived access to GitHub, an
LLM API, and later other services, without the runner ever holding a long-lived key. Security and
access control come first: who may get what, for how long, and how fast it can be taken back.

Three things this is explicitly NOT:
- An LLM host. Runners run their own models. The broker calls no model.
- A bot that reacts to webhooks. A trigger that starts a runner is a separate component.
- A general secret store. It holds the keys it signs or proxies with, and nothing else.

## 2. Where this plugs in today

Nothing of the broker exists. Related pieces on `origin/main` today:

- Sandy (`kauki-labs/sandy`) has a `CredentialProvider` seam and a GitHub App adapter that mints
  an installation token per job, refreshes it, and revokes it at teardown. The key stays on the
  sandy controller. The token reaches the guest as `$GITHUB_TOKEN`, an accepted interim risk that
  sandy's #372 (a TLS-terminating token swap) would close.
- `kauki-labs/infra` registers the `sandy-runner` GitHub Apps by hand and manages their install
  scope in `tf/cloud/02-github`. It runs a hub OpenBao and a vault VM (the hub's unseal root).
  Neither holds a signing key.
- `sandy-broker` is an HTTP tier for jobs and environments, with bearer-token clients. It brokers
  jobs, not credentials.

## 3. Requirements (fixed constraints)

- REQ-1: When a runner presents a valid client certificate and requests an action its policy
  allows, the broker SHALL issue the credential in the delivery mode the policy names for that
  credential type. (MUST)
- REQ-2: The broker SHALL deny any request that matches no policy rule. (MUST)
- REQ-3: The broker SHALL mint GitHub installation tokens narrowed to the repositories and
  permissions of the requested action. (MUST)
- REQ-4: The GitHub App private key SHALL NOT be exportable from OpenBao and SHALL NOT be present
  in broker or runner memory. (MUST)
- REQ-5: In mode A the runner SHALL NOT hold the upstream credential. (MUST)
- REQ-6: In mode B the broker SHALL record each handed-out credential as a lease and SHALL revoke
  it at expiry or teardown. (MUST)
- REQ-7: If an upstream revoke fails, the broker SHALL deny new leases to that runner, retry, and
  alert. (MUST)
- REQ-8: A capability SHALL be bound to runner identity, action, target and expiry, and SHALL be
  rejected when any of them differs. (MUST)
- REQ-9: The broker SHALL rotate held upstream credentials before they expire, run mode A proxies
  as ephemeral per-job instances, and take TLS identities from an OpenBao PKI engine with a short
  TTL. Rotation and revocation are on by default in modes A and B. (MUST)
- REQ-10: The broker SHALL log every mint, use, denial and revoke without secret values. (MUST)
- REQ-11: The broker SHOULD support adding a delivery mode or an upstream without changing the
  policy, identity or audit core. (SHOULD)

## 4. Design

Two structurally different candidates, differing on where the secret lives while a runner works:

- **Proxy only.** Every upstream sits behind a broker proxy; the runner holds only a capability.
  The secret never leaves the broker. It needs a protocol-aware proxy per upstream, and some
  clients cannot be pointed at a proxy.
- **Per-credential delivery modes.** The same core (identity, policy, signing, audit) delivers a
  credential as A, a proxied call; B, a leased copy; or C, an OIDC identity token. The mode is a
  property of the credential type and the harness, declared in policy.

The second wins. Steps, each a consequence of the one before:

1. Runners are outside the trust domain, so identity must not be a copyable secret: mTLS client
   certificates from an OpenBao PKI engine (REQ-1, REQ-9).
2. Identity selects policy. Policy maps an action and target to a credential type, a mode, and the
   narrowest scope the upstream can express (REQ-2, REQ-3).
3. The signing key for GitHub must stay in OpenBao. Transit signs the app JWT; the broker never
   holds the key (REQ-4). This depends on key import working, which Block 1 tests.
4. GitHub scopes a token only by repository set and permission set. Finer limits (one PR, one
   ref) need a proxy, so mode A is the default for GitHub actions (REQ-3, REQ-5).
5. Some clients read a token themselves (git, gh) or can mint keys on request (OpenRouter), so
   mode B exists, with leases so revocation is an OpenBao operation (REQ-6, REQ-7).
6. Mode C is a seam only until an upstream trusts the broker as an issuer.
7. Everything long-lived is rotated: held credentials, proxy instances, TLS identities, and
   capabilities (REQ-9). The one manual step is a held key with no create API (Anthropic direct).

**Considered and set aside:** hosting the broker inside the sandy workspace (little code is
shared and the signing path would sit beside guest-facing code); Kubernetes workload OIDC for
runner identity (runners may be microVMs without a service account); the hub OpenBao as key
custody (it shares a root of trust with a Cloudflare account); two apps instead of two key slots
(kept as the upgrade path).

## 5. Data model / history

- **Policy:** per runner identity, a set of rules `{action, repos or targets, credential type,
  mode, max TTL}`. Stored in the broker repo or infra, versioned, loaded at start.
- **Capability:** `{runner, action, target, expiry}` signed by the broker. Not stored.
- **Lease (mode B):** `{id, runner, credential type, upstream handle, expiry, state}`. State is
  one of active, renewing, revoking, revoked, revoke-failed. Kept in OpenBao.
- **Audit event:** append-only, no secret values: who, what, target, mode, outcome, time.

## 6. Technology & licensing

| Technology | Role | License | Status |
|-----------|------|---------|--------|
| OpenBao (transit, PKI, KV) | key custody, signing, runner CA, lease records | MPL-2.0 | in use for the hub; a dedicated instance is new |
| GitHub Apps API | installation tokens | n/a | stable REST API |
| OpenRouter management API | create and revoke LLM keys | n/a | documented |
| Anthropic Admin API | disable or archive keys; cannot create | n/a | documented |
| `rknightion/openbao-plugin-secrets-github` | fallback GitHub token minting | Apache-2.0 | v0.1.5, third party, unreviewed |
| Implementation language | broker | n/a | not decided |

## 7. Risks & technical debt

- **App key blast radius.** One app has two key slots that reach the same permission ceiling, so
  a leak of either key reaches the full ceiling on every installation. Mitigation: a minimal
  ceiling, signing through OpenBao only, separate signer policies per slot. Debt taken knowingly:
  two apps would split read and write.
- **Hot path.** In mode A the broker is in every request. It needs rate-limit awareness: an
  installation gets 5,000 requests per hour as a baseline, with secondary limits.
- **Dedicated OpenBao.** Another service to run, back up and unseal. Its unseal path is open.
- **Mode B secrets in runners.** A runner holds a usable token until revoked. Mitigation: short
  TTLs, revoke on teardown, a probe that expects 401 after revoke.
- **Manual rotation.** The GitHub app key and a held Anthropic key rotate by hand.

## 8. Open items and follow-up

- Sizing the pilot must produce: capability TTL, certificate TTL, lease TTL, probe interval,
  and proxy overhead per request.
- Out of scope but committed later: mode C with a real upstream; a Merge-gate-style audit
  dashboard; a sandy remote-provider adapter.

## 9. Rollout

1. Block 0 and the Block 1 acceptance test. Gate: transit signing passes, or the plugin path is
   chosen after its review.
2. Identity, policy and capability (Blocks 2 to 4), with the fake upstream.
3. Mode A for GitHub and the LLM proxy (Blocks 5 and 6), against the scratch repo.
4. Mode B, leases and rotation (Blocks 7 and 8), then audit (Block 9).
5. Pilot with one real runner. Admit more runners only after the abuse controls are measured.

## 10. Validation pass (self-adversarial)

What did this design open up?

- A broker that mints tokens for every installation is a high-value target. Fixed by keeping the
  key in OpenBao and the minimal ceiling above. Tracked: the dedicated OpenBao's unseal path.
- Per-credential modes mean more code paths to get wrong. Fixed by one policy and audit core and a
  shared lease ledger; each mode has its own adversarial tests (Blocks 5 to 8).
- A revoke that fails silently leaves a live token. Fixed by REQ-7 and the 401 probe.
- A capability replayed against another target or runner. Fixed by REQ-8 and its tests.
- Proxy path confusion (encoded paths, redirects, GraphQL) reaches an endpoint outside the
  action's allowlist. Fixed by a method-and-path allowlist per action and tests for each bypass
  class in Block 5. GraphQL is denied until an action needs it.
- Certificate theft. Short certificate lifetimes and per-job proxies bound it; a CRL is an open
  item.

## 11. Unresolved questions

- Unseal path for the dedicated OpenBao: decided by the maintainer.
- Whether to add a CRL on top of short certificate lifetimes: decided at the Block 2 pilot.
- Implementation language: decided before Block 2 starts.

## Sources

1. GitHub, create an installation access token: https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app (accessed 2026-11-03)
2. GitHub, generating a JWT for an app: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app (accessed 2026-11-03)
3. GitHub, revoke an installation token: https://docs.github.com/en/rest/apps/installations#revoke-an-installation-access-token (accessed 2026-11-03)
4. GitHub, REST API rate limits: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api (accessed 2026-11-03)
5. OpenBao, transit API: https://openbao.org/docs/api/secret/transit/ (accessed 2026-11-03)
6. OpenBao, transit key wrapping guide: https://openbao.org/docs/secrets/transit/key-wrapping-guide/ (accessed 2026-11-03)
7. OpenRouter, management API keys: https://openrouter.ai/docs/guides/overview/auth/management-api-keys (accessed 2026-11-03)
8. OpenRouter, Claude Code integration: https://openrouter.ai/docs/guides/coding-agents/claude-code-integration (accessed 2026-11-03)
9. Anthropic, Admin API: https://platform.claude.com/docs/en/manage-claude/admin-api (accessed 2026-11-03)
10. OpenBao GitHub secrets plugin (fork): https://github.com/rknightion/openbao-plugin-secrets-github (accessed 2026-11-03)
11. Sandy credential seam: `kauki-labs/sandy`, `docs/decisions/credential-seam.md`
