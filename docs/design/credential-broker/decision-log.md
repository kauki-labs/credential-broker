# Decision log: Credential broker

The anti-re-litigation record. Active decisions also have one file each in `docs/decisions/`.

## Verified facts

| Fact | How verified | Consequence |
|------|--------------|-------------|
| An installation token can only be narrowed, by `repositories` and `permissions`, up to 500 repos, and expires after 1 hour | GitHub REST docs (source 1) | GitHub scope stops at repo set and permission set; finer limits need a proxy |
| An app JWT must be RS256 and `exp` at most 10 minutes ahead; `iat` 60 s in the past is advised | GitHub JWT docs (source 2) | The signing step needs RS256, which is RSASSA-PKCS1-v1_5 with SHA-256 |
| `DELETE /installation/token` revokes the token that makes the call, returns 204 | GitHub REST docs (source 3) | A broker can revoke a token it handed out |
| Installation tokens get 5,000 requests per hour as a baseline; secondary limits include 900 REST points per minute | GitHub rate-limit docs (source 4) | A mode A proxy shares one budget across runners and must count requests |
| OpenBao transit signs with `rsa-2048/3072/4096` using `pkcs1v15` or `pss` and a chosen hash | OpenBao API docs (source 5) | RS256 maps to `pkcs1v15` with `sha2-256` |
| OpenBao transit can import external RSA keys, wrapped with a 4096-bit wrapping key; PKCS8 expected | OpenBao API and key-wrapping docs (sources 5, 6) | The GitHub PEM likely needs conversion; Block 1 tests the result |
| OpenRouter management keys create, update, disable and delete keys, with a spend limit, reset and expiry | OpenRouter docs (source 7) | OpenRouter keys can be mode B with full automation |
| The Anthropic Admin API can read, rename and change the status of keys but cannot create them | Anthropic docs (source 9) | Direct Anthropic keys are mode A with a manually rotated held key |
| Claude Code accepts `ANTHROPIC_BASE_URL` and sends `ANTHROPIC_AUTH_TOKEN` as a bearer token | OpenRouter integration guide (source 8) | Mode A works with the capability as the token. Not verified for opencode |
| A community OpenBao plugin mints GitHub installation tokens; it must hold the raw key | Repo page (source 10); the key-holding point is inferred from how it signs | Fallback only, after source review |

## Decisions

### D1: The broker lives in its own repo

- **Status:** Accepted
- **Why:** It guards keys that reach every installation. Own collaborators, branch protection and release rights.
- **Grounding:** `docs/decisions/standalone-repo.md`
- **Confirmation:** The repo's ruleset requires the `Merge gate` check.

### D2: The broker hosts no LLM

- **Status:** Accepted
- **Why:** Joining model cost, LLM keys and prompt-injection exposure with GitHub keys widens the blast radius.
- **Grounding:** `docs/decisions/broker-hosts-no-llm.md`
- **Confirmation:** No broker code path calls a model API for its own purposes (review gate).

### D3: Delivery mode per credential type

- **Status:** Proposed
- **Why:** Some clients cannot be proxied, some upstreams issue keys on request, some trust an issuer. One mode cannot serve all.
- **Grounding:** Verified facts rows for GitHub, OpenRouter, Anthropic and Claude Code.
- **Confirmation:** Blocks 5 to 8 pass for their mode; a new mode is added without changing Blocks 2 to 4 and 9.

### D4: Transit signing first, plugin as fallback

- **Status:** Proposed
- **Why:** Transit signs without exposing the key. The plugin holds the raw PEM.
- **Grounding:** OpenBao transit and import docs (sources 5, 6).
- **Confirmation:** Block 1 passes. If it fails on import, the plugin gets a source review before any use.

### D5: mTLS runner identity from an OpenBao PKI engine

- **Status:** Accepted
- **Why:** Runners may be microVMs without a Kubernetes service account; a bearer token is copyable.
- **Grounding:** `docs/decisions/mtls-runner-identity.md`
- **Confirmation:** Block 2 tests.

### D6: Dedicated OpenBao, narrow scope

- **Status:** Proposed
- **Why:** A separate trust domain from the hub. It does signing and machine identity only.
- **Grounding:** `docs/decisions/dedicated-openbao-custody.md`
- **Confirmation:** The instance has no mount beyond transit, PKI and lease KV, and its policies are separate per function.

### D7: One app per owner with two key slots

- **Status:** Accepted
- **Why:** Gives rotation and per-signer revocation. Both keys reach the full ceiling, so it is an accepted risk.
- **Grounding:** `docs/decisions/one-app-two-key-slots.md`
- **Confirmation:** Signer policies keep each slot's signing separate (Block 1 adversarial test).

### D8: Rotation and revocation on by default in modes A and B

- **Status:** Proposed
- **Why:** A credential that cannot be taken back is a standing grant. OpenBao drives the steps it can.
- **Grounding:** Verified facts rows for GitHub revoke and OpenRouter delete.
- **Confirmation:** Block 7 and 8 tests.

### D9: Fail closed on a failed upstream revoke

- **Status:** Proposed
- **Why:** A revoke that did not happen leaves a live token. Denying new leases to that runner bounds it.
- **Grounding:** REQ-7.
- **Confirmation:** Block 7 adversarial test.

## Considered and set aside

Do not re-propose without new information.

| Option | Why set aside |
|--------|---------------|
| Broker inside the sandy workspace | Little shared code; signing path beside guest-facing code and outside contributors |
| Kubernetes workload OIDC for runners | Ties runners to one cluster; microVMs have no service account |
| Hub OpenBao for key custody | Shares a root of trust with a Cloudflare account that holds DNS, tunnel and R2 state |
| Sealed key file on the broker host | The broker process and host root can read it |
| Proxy for every upstream | Some clients cannot be pointed at a proxy |
| Two apps (read, write) now | Kept as the upgrade path; costs a second registration and install per owner |
| Permissive license | A hosted fork could stay closed; sandy is already AGPL with a commercial option |

## Open items

| Item | Concrete close condition |
|------|--------------------------|
| Transit import and signing of the GitHub key | Block 1 passes against a test installation |
| Unseal path of the dedicated OpenBao | The maintainer picks one and an ADR records it |
| Certificate revocation | The Block 2 pilot shows short lifetimes alone suffice, or a CRL is added |
| opencode with a capability as the API key | A test run with opencode against the mode A LLM proxy |
| GitHub app key generation API | Confirm against GitHub docs; otherwise rotation stays manual |
| Pilot values (TTLs, probe interval) | Measured in the pilot and written into the spec |
