# external GitHub App (interim)

A GitHub App that lets an operator mint short-lived GitHub tokens for agents outside the
platform, read or read-write. It is the interim version of this broker: the private key sits in
a file under the operator's uid and `scripts/github-app-token` signs with it. The broker later
moves the key into OpenBao and replaces the script (see the
[spec](../design/credential-broker/credential-broker-spec.md), Block 1).

| Item | Value |
|------|-------|
| App name | `external actor` (acts as `external-actor[bot]`, slug to be confirmed in the App settings). `external` is the short name used for it in this repo; the name `external` was not available on GitHub |
| Owner | The operator's personal account. The App is public, so it installs on `kauki-labs` and on the personal account. A private App installs only on its owner |
| Permissions (ceiling) | `metadata` read; `contents`, `pull_requests`, `issues` write |
| Manifest | [`apps/external.kauki-labs.manifest.json`](../../apps/external.kauki-labs.manifest.json) |
| Install scope | On `kauki-labs`: `sandy`, `credential-broker`. Set in `tf/cloud/02-github/external_app.tf` in `kauki-labs/infra` and in the script's allowlist. Change both together. The personal-account install is managed in the GitHub UI |
| Installation id | `TF_VAR_external_installation_id_kauki_labs` in infra's `tf/cloud/secrets.yaml` |

## Register the App (once)

GitHub has no API to create an App, so this is manual. It uses the App-Manifest flow. Run it
from an empty temporary directory, never from a repository.

1. Add `"redirect_url": "http://localhost:8080/"` to a copy of the manifest. Start the catcher
   bound to loopback: `cd "$(mktemp -d)" && python3 -m http.server 8080 --bind 127.0.0.1`.
2. POST the manifest as the form field `manifest` to
   `https://github.com/settings/apps/new?state=external` (or the organization's `.../settings/apps/new` URL to make the org the owner).
3. Click **Create GitHub App**. Copy the `code` from the redirect URL (valid about 1 hour).
4. Exchange it without printing the secrets. The response holds the private key, a client
   secret and a webhook secret:
   ```sh
   mkdir -p ~/.config/kauki && chmod 700 ~/.config/kauki
   d=$(mktemp -d)
   (umask 077
    curl -fsS -X POST -H "Accept: application/vnd.github+json" \
      "https://api.github.com/app-manifests/<CODE>/conversions" > "$d/resp.json"
    jq -r .pem "$d/resp.json" > ~/.config/kauki/external.pem)
   jq '{id, client_id, html_url}' "$d/resp.json"   # only the non-secret ids
   rm -rf "$d"
   ```
5. Install the App from `https://github.com/apps/<slug>`. On `kauki-labs` choose **Only select
   repositories** (required, not "All repositories"): `sandy` and `credential-broker`. Repeat for
   the personal account with the repos you want. Each installation has its own id, in the install
   settings URL (`.../settings/installations/<ID>`).
6. Save the `.pem` and ids in KeePass, entry `Infrastructure/Credentials/external app private key`.
   Put the installation id in infra's `tf/cloud/secrets.yaml` as
   `TF_VAR_external_installation_id_kauki_labs`, then apply `tf/cloud/02-github` there.
7. Run the live acceptance suite (below). It checks the install scope and the permission split.

## Register an App for another account or organization

Each installer owns their own App and key (see the
[decision](../decisions/installer-owned-apps.md)). The helper runs GitHub's manifest flow for you,
so there is no form to POST and no code to race:

```sh
scripts/github-app-register                                   # personal account, no webhook yet (broker at localhost)
scripts/github-app-register --org my-org --broker-url https://broker.example.org
scripts/github-app-register --public                          # installable on other accounts too
```

Open the printed `http://127.0.0.1:<port>/<token>/` link and click **Create GitHub App**. GitHub redirects
back, and the helper exchanges the one-time code at once. It writes `<slug>.pem` and
`<slug>.webhook-secret` at mode 0600 to `~/.config/kauki` (never overwriting an existing file; a
name clash gets a numbered sibling) and prints only the ids. The key is written first, so a
problem with any other field cannot lose it. It serves on loopback only, behind a random token in
the URL path, and checks a random `state` value and the `Host` header. Other users on a shared
machine can reach the port but not the token. It discards the client secret. If the run fails after
GitHub has created the App, the App exists without a saved key: open its settings and generate a
new key, or delete it.

GitHub's form refuses a webhook URL that is not reachable over the public Internet ("Hook url is not
supported because it isn't reachable over the public Internet (localhost)"). So with the default
broker URL `http://localhost:8080`, or any loopback, private or `.local` address, the helper registers
the App **without a webhook, events or Setup URL**. With a public `--broker-url` it sets the
webhook to `<url>/github/webhook`, the Setup URL to `<url>/github/setup` and subscribes the events.
To add them later, see "Webhook and broker endpoint". Then install the App
and follow "Get a token" below with the new key and ids. The minter's `allowed_repos` line is fixed
to `sandy` and `credential-broker`: edit it to your own repository names, or it refuses them.

## App settings sheet

Values for the App's **General** page, for an App that already exists. The manifest carries the
same values for a fresh registration.

| Field | Value |
|-------|-------|
| GitHub App name | `external actor` |
| Description | Connects GitHub to a credential broker. Sends install and repository events to the broker and lets it mint short-lived, narrowly scoped installation tokens for external agents. The broker address is set after installation and defaults to localhost. |
| Homepage URL | `https://github.com/kauki-labs/credential-broker` |
| Callback URL, user authorization during install, device flow | empty and off. The App acts as itself, never as a user |
| Setup URL | `<broker URL>/github/setup`, once the broker has a public URL. Leave empty until then |
| Redirect on update | on |
| Webhook: Active | on |
| Webhook URL | `<broker URL>/github/webhook`, public only (GitHub refuses `localhost`). Set with `scripts/github-app-webhook`, below |
| Webhook secret | set with `scripts/github-app-webhook`, never typed into a form you screenshot |
| SSL verification | enabled |
| Repository permissions | Contents, Issues, Pull requests: read and write. Metadata: read-only. Nothing else |
| Organization and account permissions | none |
| Subscribe to events | Issues, Issue comment, Pull request, Pull request review, Push, Create, Delete. `installation` and `installation_repositories` arrive without a subscription |
| Where can this GitHub App be installed? | Any account (the label is at the bottom of General; if it is not there, look under Advanced) |

## Webhook and broker endpoint

The App holds exactly two broker addresses, the webhook URL and the Setup URL. The broker's own
address and settings live in the broker, not in GitHub, so pointing the App at a broker is two
changes, and nothing else in the App depends on it.

Both must be public: GitHub refuses a `localhost` webhook URL, and its servers could not deliver to
it anyway. Until the broker has a public host or a tunnel in front of it (for example smee.io), the
App has no webhook. Once it does:

1. In the App settings, General, tick **Webhook: Active** and set the Setup URL.
2. Under Permissions & events, subscribe to the events (issues, issue comment, pull request, pull
   request review, push, create, delete).
3. Set the URL and secret with the script. It uses the secret file the helper wrote.

```sh
export GH_APP_ID=<client id> GH_APP_KEY_FILE=~/.config/kauki/external-actor.pem
export GH_APP_WEBHOOK_SECRET_FILE=~/.config/kauki/external-actor.webhook-secret   # one line, 16+ characters, mode 0600
scripts/github-app-webhook set https://broker.example.org/github/webhook
scripts/github-app-webhook show
```

The API changes only the webhook URL and secret. The Active flag, the event list and the Setup
URL are UI-only. To move to a real broker, run `set` with the new URL and change the Setup URL in
the App's General page. The receiver must check `X-Hub-Signature-256` against the secret and must
only record events, never act on them with the App's write ceiling.

## Get a token (operator side)

```sh
export GH_APP_ID=<client id>
export GH_APP_KEY_FILE=~/.config/kauki/external.pem        # mode 0600 or 0400
export GH_APP_INSTALLATION_ID=<installation id>
scripts/github-app-token sandy credential-broker           # read, 1 hour
scripts/github-app-token --write sandy                     # contents, pull_requests, issues write
# hand a token to an agent without it touching a file or argv:
GH_TOKEN=$(scripts/github-app-token sandy) <agent command>
```

The script's permission sets are fixed, it refuses repos outside its allowlist, and it refuses a
key file with a loose mode.

## Revoke a token early

```sh
GH_TOKEN=$TOKEN gh api -X DELETE /installation/token
```

## Acceptance tests

| Suite | Runs | Covers |
|-------|------|--------|
| `tests/test_github_app_token.py` | CI and locally. Offline, fake `curl` | JWT claims and signature, exact read and write request bodies, curl hardening, hostile inputs, key-file modes and symlinks, upstream failure modes, no secret in argv, xtrace or `TMPDIR` |
| `tests/test_github_app_webhook.py` | CI and locally. Offline, fake `curl` | JWT, exact PATCH body, secret never on argv or in output, body file mode and cleanup, URL and secret validation, key and secret file modes, upstream failures, xtrace |
| `tests/test_github_app_register.py` | CI and locally. Offline, injected exchange, real loopback sockets | Manifest fields and permissions, hostile inputs, form round trip, secret path token, state and Host checks, one-shot callback that ends the run on any failure, the key written first, idle connections, files at 0600, no overwrite or symlink follow, no secret in output |
| `tests/test_acceptance_live.py` | CI and locally. Runs the live suite against a fake GitHub | An honest fake passes; a public sibling repo skips the private check; a wrong install, an over-wide App, a read token that can write and a failing revoke each fail the suite; the suite leaves no branch and revokes every token; it refuses to run without `ACCEPT_LIVE=1` |
| `tests/acceptance_live.py` | Operator only, after registration. `ACCEPT_LIVE=1` plus the three env vars | Against real GitHub: the install is "Only select repositories" and reaches exactly `sandy` and `credential-broker`; the App permissions equal the ceiling; a read token reads and cannot write; a write token writes to its repo only; early revoke returns 401 |

Run every offline suite with `nix run .#tests` from the repo root (pytest with xdist; pass pytest arguments after `--`, for example `-k webhook`). CI runs the same command. Run the live suite with `ACCEPT_LIVE=1 python3 -I tests/acceptance_live.py` (add `-v` for one line per check).

The live suite creates and deletes one branch `acceptance/<time>-<pid>` on `credential-broker`.
It stays out of CI because the key must never be there.

## Limits of this interim setup

- **The key is only as safe as the uid that owns it.** An agent that runs as that uid, or can
  run the script as it, can read the key and mint write tokens for every installed repo until
  the key is rotated. Mint tokens yourself and hand them over. Do not run the script from an
  agent session.
- A leak of the key reaches the full write ceiling on every installed repo.
- There is no per-runner identity, policy or audit log. The broker adds those.
- The script's environment hardening covers tracing and curl config. It cannot stop a caller
  that controls `PATH` or `BASH_ENV` from running its own `openssl` and reading the key.
- Rotating the key is manual: generate a new key in the App settings, replace the file, delete the old key.
