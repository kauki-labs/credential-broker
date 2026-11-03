# external GitHub App (interim)

A GitHub App that lets an operator mint short-lived GitHub tokens for agents outside the
platform, read or read-write. It is the interim version of this broker: the private key sits in
a file under the operator's uid and `scripts/github-app-token` signs with it. The broker later
moves the key into OpenBao and replaces the script (see the
[spec](../design/credential-broker/credential-broker-spec.md), Block 1).

| Item | Value |
|------|-------|
| App name | `external` (acts as `external[bot]`) |
| Owner | `kauki-labs` (org). A private App installs only on its own owner |
| Permissions (ceiling) | `metadata` read; `contents`, `pull_requests`, `issues` write |
| Manifest | [`apps/external.kauki-labs.manifest.json`](../../apps/external.kauki-labs.manifest.json) |
| Install scope | `sandy`, `credential-broker`. Set in `tf/cloud/02-github/external_app.tf` in `kauki-labs/infra` and in the script's allowlist. Change both together |
| Installation id | `TF_VAR_external_installation_id_kauki_labs` in infra's `tf/cloud/secrets.yaml` |

## Register the App (once)

GitHub has no API to create an App, so this is manual. It uses the App-Manifest flow. Run it
from an empty temporary directory, never from a repository.

1. Add `"redirect_url": "http://localhost:8080/"` to a copy of the manifest. Start the catcher
   bound to loopback: `cd "$(mktemp -d)" && python3 -m http.server 8080 --bind 127.0.0.1`.
2. POST the manifest as the form field `manifest` to
   `https://github.com/organizations/kauki-labs/settings/apps/new?state=external`.
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
5. Install the App on `kauki-labs` with **Only select repositories** (required, not "All
   repositories"): `sandy` and `credential-broker`. The installation id is in the install
   settings URL (`.../settings/installations/<ID>`).
6. Save the `.pem` and ids in KeePass, entry `Infrastructure/Credentials/external app private key`.
   Put the installation id in infra's `tf/cloud/secrets.yaml` as
   `TF_VAR_external_installation_id_kauki_labs`, then apply `tf/cloud/02-github` there.
7. Run the live acceptance suite (below). It checks the install scope and the permission split.

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
| `tests/test-github-app-token.sh` | CI (`Merge gate`) and locally. Offline, fake `curl` | JWT claims and signature, exact read and write request bodies, curl hardening, hostile inputs, key-file modes and symlinks, upstream failure modes, no secret in argv, xtrace or `TMPDIR` |
| `tests/acceptance-live.sh` | Operator only, after registration. `ACCEPT_LIVE=1` plus the three env vars | Against real GitHub: install scope is exactly `sandy` and `credential-broker`; a read token reads and cannot write; a write token writes to its repo only; no permission outside the ceiling; early revoke returns 401 |

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
