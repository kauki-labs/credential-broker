---
title: Runners get a capability, not a GitHub token
status: accepted
project: credential-broker
updated: 2026-11-03
---

# Runners get a capability, not a GitHub token

**Context.** A GitHub installation token is scoped by repository set and permission set, and
expires after one hour. GitHub has no scope below that: no per-branch, per-path, per-issue or
per-PR token. The credential must also be granular by action and target.

**Decision.** A runner requests an action on a target (for example `pr.comment` on one PR). The
broker checks policy, mints a narrow installation token itself, and keeps it. The runner gets a
short-lived signed capability bound to the action, the target and the runner's identity. The
runner points its GitHub API base URL and git remote at the broker. The broker checks each
request against the capability, injects the token, forwards the call, and logs it.

**Consequences.**
- A leaked runner credential reaches one action on one target until it expires.
- The broker becomes a hot path and must speak the GitHub REST API and git smart-HTTP. GraphQL
  stays denied until an action needs it.
- Every request is checked against an allowlist of method and path per action.

**Set aside.** Native tokens only [^native]: they stop at repository and permission scope.
Rulesets alone for branch limits: they help for pushes but not for comments or reviews.

[^native]: Enough for repo-wide actions. Kept as the model for the token the broker mints
    internally, which is always narrowed by repository and permission.
