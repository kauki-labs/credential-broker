# Contributing

This is the short version. [`AGENTS.md`](AGENTS.md) is the always-current guide, and the design
lives in [`docs/`](docs/overview.md).

The repo is pre-code. Open an issue before a large change.

## Licensing of contributions (CLA)

credential-broker is dual-licensed: [AGPL-3.0-only](LICENSE) for open-source use, and a separate
commercial license for use that cannot meet the AGPL's terms (see [COMMERCIAL.md](COMMERCIAL.md)).
For the project to offer that commercial license, it must hold sufficient rights in every
contribution. By submitting a contribution (a pull request, patch, or any other work), you agree
to the following with the project's current maintainer, **Tibor Csóka** ("the Maintainer"). This
grant runs to the Maintainer and to any successor who takes over maintainership of this project
(by transfer, assignment, or otherwise); it does not need to be re-granted on a change of
maintainer:

- **Grant.** You grant the Maintainer a perpetual, worldwide, non-exclusive, royalty-free,
  irrevocable license to reproduce, prepare derivative works of, publicly display and perform,
  sublicense, and distribute your contribution and derivative works, **with the right to
  sublicense and relicense it under any license, including AGPL-3.0, other open-source licenses,
  and proprietary/commercial licenses.**
- **Patents.** You grant the Maintainer and recipients a perpetual, worldwide, non-exclusive,
  royalty-free, irrevocable patent license for any patent claims you can license that your
  contribution necessarily infringes, alone or combined with the project.
- **You keep ownership.** This is a license, not an assignment; you retain all right, title, and
  interest in your contribution and may use it however you wish.
- **Representations.** Each contribution is your original work, you are entitled to grant these
  licenses, and if your employer has rights to your work you have permission to contribute.

If you do not agree, do not submit a contribution.

> These terms are the project's current CLA and may be revised; a contribution is accepted under
> the version in effect when it is submitted.

## Commits and PRs

- Use a [Conventional Commits](https://www.conventionalcommits.org) title. CI checks it. Allowed
  types: `fix`, `feat`, `build`, `chore`, `ci`, `docs`, `style`, `refactor`, `perf`, `test`.
- One logical change per PR.
- Run `python3 scripts/validate_docs.py .` before a PR that touches `docs/` or `AGENTS.md`.
- Never include a key, token or `.env*` file.

## Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
