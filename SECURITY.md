# Security policy

credential-broker holds GitHub App private keys and issues credentials to external runners. Its
key custody, runner authentication, capability checks and request proxy are load-bearing, so we
take reports against them seriously.

## Reporting a vulnerability

Report privately, not in a public issue:

- Preferred: GitHub's private vulnerability reporting on this repo, under
  **Security → Report a vulnerability**
  (<https://github.com/kauki-labs/credential-broker/security/advisories/new>).
- Or email **tibor.csoka@kauki.xyz** with the details.

Please include a description, the affected version or commit, and the smallest steps that
reproduce it. A proof of concept helps; a working exploit is not required, and we would rather
you not post one publicly.

We aim to acknowledge a report within three working days and to agree a disclosure timeline with
you. We will credit you in the advisory unless you ask us not to.

## Scope

In scope: anything that lets a runner obtain a credential or capability it was not issued, reuse
a capability against another target, repository or runner identity, reach a GitHub endpoint
outside its action's allowlist, read or extract an app key or installation token, or make the
audit log misreport a mint or a denial.

Out of scope: issues that require an already-privileged host, and the behaviour of a runner's own
model or payload.

## Supported versions

Pre-release. There is no released version yet.
