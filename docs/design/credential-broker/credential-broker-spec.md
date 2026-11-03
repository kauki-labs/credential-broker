# Credential broker: spec

Design amendments (grounded deviations from the proposal) go here and supersede it.

## Block index

| # | Block | Runs on | Depends on | Satisfies |
|---|-------|---------|-----------|-----------|
| 0 | Preconditions | pilot | - | REQ-4, REQ-9 |
| 1 | Transit signing acceptance test | pilot | 0 | REQ-3, REQ-4 |
| 2 | Runner identity (mTLS from OpenBao PKI) | fake + pilot | 0 | REQ-1, REQ-9 |
| 3 | Policy engine | fake | 2 | REQ-1, REQ-2 |
| 4 | Capability issue and verify | fake | 3 | REQ-8 |
| 5 | Mode A, GitHub proxy | fake + pilot | 1, 4 | REQ-3, REQ-5 |
| 6 | Mode A, LLM proxy | fake + pilot | 4 | REQ-5 |
| 7 | Mode B, lease ledger | fake + pilot | 1, 3 | REQ-6, REQ-7 |
| 8 | Rotation | fake + pilot | 5, 6, 7 | REQ-9 |
| 9 | Audit log | fake | 3 | REQ-10 |

Mode C is a seam only. It has no block until an upstream trusts the broker as an issuer (REQ-11).
Blocks 2 and 3 can run in parallel with Block 1. Blocks 5 and 6 can run in parallel.

## Block 0: Preconditions

**Purpose.** Stand up what every later block assumes: a throwaway OpenBao, a test GitHub App, a
scratch repo.
**Architecture.** An OpenBao dev instance with transit and PKI mounted. A GitHub App with two key
slots, installed on one scratch repo in `kauki-labs`. An OpenRouter management key with a low
spend cap. A fake upstream that answers like GitHub and OpenRouter for non-pilot runs.
**Interfaces.** Input: app id, installation id, the key PEM, OpenRouter management key. Output: a
mounted transit and PKI, a reachable fake upstream. Config surface: addresses and ids, never the
secrets themselves in a file in the repo.
**Satisfies:** REQ-4, REQ-9.

Verification (positive) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| OpenBao reachable | Start dev OpenBao, mount transit and PKI | Both mounts list as enabled |
| Scratch install works | Mint a token with a hand-made JWT | 201 from GitHub on the scratch repo |
| Fake upstream answers | Call the fake create-token and key endpoints | Responses match the documented shape |

Adversarial (negative) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Secret in repo | Run a secret scan over the working tree after setup | Zero findings |
| Wrong installation id | Request a token for an installation that does not exist | 404 or 403, and the setup script fails visibly |

## Block 1: Transit signing acceptance test

**Purpose.** Prove the GitHub app key can live in OpenBao transit and still mint tokens. This is
the hinge decision.
**Architecture.** Import the key as non-exportable, `allow_rotation=false`. Convert PKCS1 to PKCS8
first if the import requires it. Sign the JWT signing input in transit with `pkcs1v15` and
`sha2-256`, base64url the signature, call the create-token endpoint.
**Interfaces.** Input: key PEM, app id, installation id, repo, permission. Output: an installation
token or a typed error. Config surface: transit mount path, key name per slot.
**Satisfies:** REQ-3, REQ-4.

Verification (positive) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Import and sign | Import the key, sign a valid JWT (`iat` 60 s back, `exp` 9 min) | GitHub returns 201 and a token |
| Narrowing | Request one repo and `pull_requests: write` | The token works on that repo and that permission only |
| Two slots | Import a second key under another name, mint with each | Both mint; each uses only its own transit key |

Adversarial (negative) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Wrong scheme | Sign with `pss` | GitHub rejects with 401 |
| Long expiry | Build a JWT with `exp` 15 minutes ahead | GitHub rejects |
| Export attempt | Call the transit export endpoint for the key | Denied by OpenBao (assert the denial is observable) |
| Slot isolation | Use the slot 1 signer policy against the slot 2 key | Permission denied |
| Over-ceiling request | Ask for a permission the app was not granted | GitHub refuses; broker surfaces a typed error, not a token |
| Key absent in process | Dump the broker process memory and the working tree after a mint | The PEM is not found |

## Block 2: Runner identity (mTLS from OpenBao PKI)

**Purpose.** Authenticate runners with short-lived client certificates.
**Architecture.** An OpenBao PKI role issues client certificates with a short TTL. The broker
terminates mTLS and derives a runner identity from the certificate subject.
**Interfaces.** Input: a TLS client certificate. Output: a runner identity or a rejection. Config
surface: the CA, the allowed subject pattern, the certificate TTL (pilot value).
**Satisfies:** REQ-1, REQ-9.

Verification (positive) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Valid cert | Present a fresh certificate from the PKI role | Identity resolves to the subject |
| Renewal | Renew before expiry | A new certificate works; the old one works until expiry only |

Adversarial (negative) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Expired cert | Present a certificate past its TTL | Rejected |
| Foreign CA | Present a certificate from another CA | Rejected |
| Subject outside pattern | Valid cert, subject not allowed | Rejected with a logged denial |
| No cert | Connect without a client certificate | Handshake fails |

## Block 3: Policy engine

**Purpose.** Map a runner identity and a requested action to a credential type, a mode and the
narrowest scope.
**Architecture.** Rules `{identity, action, targets, credential type, mode, max TTL}` loaded from
versioned files. Evaluation is deny by default.
**Interfaces.** Input: identity, action, target, requested TTL. Output: an allow with mode and
scope, or a denial with a reason code. Config surface: the policy files.
**Satisfies:** REQ-1, REQ-2.

Verification (positive) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Allowed action | Identity with a rule for `pr.comment` on repo X | Allow, mode A, scope repo X |
| TTL clamp | Request a TTL above the rule's max | Granted TTL equals the max |

Adversarial (negative) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| No rule | Identity with no matching rule | Deny with reason `no-rule` |
| Target outside rule | Right action, other repo | Deny |
| Widening request | Ask for more permissions than the action maps to | Deny; the request body never widens scope |
| Empty or missing policy file | Start with no policy | Broker refuses to start, or denies everything and says so |

## Block 4: Capability issue and verify

**Purpose.** Issue a short-lived signed capability and verify it on each proxied request.
**Architecture.** A capability holds `{runner, action, target, expiry}`, signed with a broker
capability key held in OpenBao transit. The proxy verifies signature, expiry and binding.
**Interfaces.** Input: a policy allow. Output: an opaque capability. Verify input: capability plus
the presenting identity and the request. Config surface: capability TTL (pilot value).
**Satisfies:** REQ-8.

Verification (positive) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Round trip | Issue, then verify with the same identity and target | Valid |
| Expiry | Verify after the expiry time | Invalid |

Adversarial (negative) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Replay on another target | Present it for another repo or PR | Rejected |
| Replay by another runner | Present it with a different client certificate | Rejected |
| Tampered scope | Edit a field and keep the signature | Rejected |
| Expired but clock skewed | Verify within a small skew window past expiry | Rejected beyond the stated skew only |

## Block 5: Mode A, GitHub proxy

**Purpose.** Forward a runner's GitHub calls with the broker-held token, checked against the
capability.
**Architecture.** A per-job proxy instance. REST: method and path allowlist per action. Git:
smart-HTTP, a push allowed only to the ref in the capability. The proxy holds the installation
token from Block 1 and refreshes it before expiry. GraphQL is denied.
**Interfaces.** Input: an HTTP request plus a capability. Output: the forwarded response or a
denial. Config surface: the per-action allowlists.
**Satisfies:** REQ-3, REQ-5.

Verification (positive) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Comment on the target PR | `POST .../issues/123/comments` with a `pr.comment` capability for #123 | 201 from GitHub |
| Push to the allowed ref | `git push` to `refs/heads/ext/x` with a `code.push` capability | Accepted |
| Token not exposed | Capture all traffic and the runner's environment | The installation token never appears |

Adversarial (negative) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Other PR | Same capability, `.../issues/124/comments` | Denied |
| Other repo | Same capability, another repository path | Denied |
| Encoded path | `%2e%2e`, double slash, mixed case | Denied |
| Redirect | An upstream redirect to another path | Not followed outside the allowlist |
| GraphQL | `POST /graphql` | Denied |
| Push to another ref | `git push` to `main` | Rejected by the proxy |
| Header smuggling | Runner supplies its own `Authorization` header | Stripped; the broker's token is used |
| Rate exhaustion | One runner floods the proxy | That runner is throttled; others keep the shared budget |

## Block 6: Mode A, LLM proxy

**Purpose.** Forward LLM calls to Anthropic or OpenRouter with the broker-held key.
**Architecture.** A proxy on the Anthropic-compatible path. The runner sets the base URL and uses
the capability as the bearer token (`ANTHROPIC_AUTH_TOKEN` for Claude Code). The proxy swaps in
the held key and logs token counts, not content.
**Interfaces.** Input: an API request plus a capability. Output: the forwarded response. Config
surface: the model allowlist, the per-runner spend cap.
**Satisfies:** REQ-5.

Verification (positive) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Claude Code through the proxy | Set base URL and token to the capability | A normal completion |
| Streaming | A streamed response | Delivered unbuffered to the runner |
| Held key not exposed | Capture the runner's environment and traffic | The real key never appears |

Adversarial (negative) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Model not allowed | Request a model outside the allowlist | Denied |
| Spend cap | Exceed the per-runner cap | Further calls denied, logged |
| Key header injection | Runner sends its own `x-api-key` | Stripped |
| Expired capability mid-stream | Capability expires during a long stream | The stream ends within the stated margin and new calls fail |

## Block 7: Mode B, lease ledger

**Purpose.** Hand over a leased credential and take it back reliably.
**Architecture.** Each handed-out credential is a lease record in OpenBao: GitHub installation
tokens (revoked with `DELETE /installation/token`) and OpenRouter keys (created with a limit and
expiry, deleted by hash). A revoke worker handles expiry, teardown and anomalies. After a revoke,
a probe expects a 401. A failed revoke marks the runner revoke-failed.
**Interfaces.** Input: a policy allow in mode B. Output: a credential and a lease id. Operations:
renew, release, revoke-all-for-runner. Config surface: lease TTL, probe interval (pilot values).
**Satisfies:** REQ-6, REQ-7.

Verification (positive) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Hand over and use | Lease an OpenRouter key with a low limit | The key completes a call |
| Release | Runner releases | The key is deleted; the probe sees a failure response |
| Expiry | Let a lease expire | Revoked without runner action |
| Revoke all | Revoke a runner's certificate | All its leases revoked in one operation |

Adversarial (negative) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Revoke fails | Make the upstream revoke return an error | New leases to that runner denied; retry; alert raised (assert all three) |
| Token still works after revoke | Fake upstream keeps accepting the token | Probe fails the check and raises an alert, not a silent success |
| Runner exits without release | Kill the runner | The TTL revokes it |
| Lease record lost | Delete a lease record in the store | Credential still expires by its own TTL; broker reports the orphan |

## Block 8: Rotation

**Purpose.** Rotate everything long-lived that the broker holds or runs.
**Architecture.** Held upstream credentials re-minted before expiry; proxy instances created per
job and torn down at the end; client and proxy TLS identities renewed from the OpenBao PKI;
capabilities re-issued. A manual path covers the Anthropic key and the GitHub app key: a new
transit key slot is imported and the old one retired.
**Interfaces.** Input: schedule and expiry events. Output: rotated credentials with no dropped
request. Config surface: refresh margins (pilot values).
**Satisfies:** REQ-9.

Verification (positive) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Held token refresh | Run a proxy across a token expiry (fake clock) | No request fails; a new token is in use |
| Per-job proxy | Finish a job | The proxy instance is gone and its held token is revoked |
| Cert renewal | Renew the proxy certificate under load | No dropped connection |
| App key slot swap | Import a new slot and retire the old | Minting continues on the new slot |

Adversarial (negative) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Refresh fails | Upstream refuses the new mint | The proxy denies new calls once the old token expires; alert raised |
| Old slot after retire | Sign with the retired slot | Denied |
| Rotation storm | Many runners rotate at once | Stays inside the upstream rate budget (assert the request count) |

## Block 9: Audit log

**Purpose.** Record every mint, use, denial and revoke without secrets.
**Architecture.** Append-only events with who, what, target, mode, outcome, time. A redaction
filter drops token-shaped values before write.
**Interfaces.** Input: events from Blocks 2 to 8. Output: a log stream or file. Config surface:
sink and retention.
**Satisfies:** REQ-10.

Verification (positive) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Mint, use, deny, revoke | Run one of each | Four events with correct fields |

Adversarial (negative) tests:

| Test | Setup / Inject | Expected result |
|------|----------------|-----------------|
| Secret in an error | Force an upstream error that echoes the token | The event holds no token value |
| Log write fails | Make the sink unavailable | Mints stop (fail closed) rather than proceed unlogged |

## Escalation appendix

Never hand-build a pipeline. Off-the-shelf options, each matched to a different shortfall:

| Tool | License | Adds | Cost | Reach for it when |
|------|---------|------|------|-------------------|
| `rknightion/openbao-plugin-secrets-github` | Apache-2.0 | GitHub token minting inside OpenBao | Holds the raw key; third-party; needs a source review | Block 1 fails on key import |
| A TLS-terminating egress proxy (sandy #372) | AGPL-3.0 | In-flight token swap for harnesses that will not take a base URL | Not built yet | A harness cannot use mode A and mode B is too weak |
| An existing LLM gateway (for example a hosted AI gateway) | varies | Spend caps and logging for the LLM path | Another trust boundary | Mode A for LLM outgrows a custom proxy |

## Acceptance criteria

The end-to-end gate:

1. Block 1 passes against a test installation. This is the hinge measurement.
2. One real runner, authenticated by a short-lived client certificate, completes one GitHub task
   in mode A and one OpenRouter task in mode B against the scratch repo and a capped key.
3. Every adversarial test above fails closed and leaves an audit event.
4. Revoking the runner's certificate revokes its live leases and the probe confirms it.
5. A kill of the runner mid-job leaves no live credential after the lease TTL.

Numbers reserved for the pilot to produce: capability TTL, client-certificate TTL, lease TTL,
revoke-probe interval, refresh margin, and proxy overhead per request.
