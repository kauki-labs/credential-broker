# credential-broker

Issues short-lived, action-scoped GitHub credentials to external runners. The broker holds the
GitHub App keys; runners hold none. Pre-service: the design is in [`docs/`](docs/overview.md); an interim operator-side token minter for the `external` App is in [`scripts/`](docs/runbooks/external-app.md).

Licensed under [AGPL-3.0-only](LICENSE), with a commercial license available
([COMMERCIAL.md](COMMERCIAL.md)). Report vulnerabilities per [SECURITY.md](SECURITY.md).
Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md).
