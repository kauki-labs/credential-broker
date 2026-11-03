# credential-broker agent instructions

Single source of truth for AI coding agents (Claude Code reads this natively; Copilot reads
it as `AGENTS.md`). Start in [`docs/`](docs/).

A broker that mints short-lived, action-scoped GitHub credentials for external runners. It
holds the GitHub App keys so runners never do. Pre-service: the design is docs; the only code is the interim operator-side token minter for the `external` App ([runbook](docs/runbooks/external-app.md)).

## Read first

- [`docs/overview.md`](docs/overview.md): what this is, the layout, the status.
- [`docs/decisions/`](docs/decisions/): why it is built this way. Read before changing a
  load-bearing choice; the alternatives listed there were already set aside.

## Essentials

- Never commit a private key, token, certificate key, or `.env*` file. `.gitignore` blocks the
  common names, but check every `git add` by hand.
- Stage explicit paths. Never `git add -A` or `git add .`.
- Branches: `<nick>/<type>/<component>/<description>`. Titles: Conventional Commits (CI checks).
- Never push to `main`. Open a PR; the merge stays with the maintainer.
- Run `python3 scripts/validate_docs.py .` before a PR that touches `docs/` or `AGENTS.md`.
- Run `for t in tests/test-*.sh; do bash "$t"; done` before a PR that touches `scripts/`. `tests/acceptance-live.sh` is operator-only (needs the App key); CI never runs it.
- Infra (the GitHub App registrations, OpenBao, deployment) lives in `kauki-labs/infra`, not here.
