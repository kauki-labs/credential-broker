#!/usr/bin/env bash
# Live acceptance tests for the `external` GitHub App, run by the operator after registering it.
# Talks to the real api.github.com with real tokens from scripts/github-app-token.
# It touches one throwaway branch on credential-broker and deletes it again.
#
#   ACCEPT_LIVE=1 GH_APP_ID=... GH_APP_KEY_FILE=... GH_APP_INSTALLATION_ID=... tests/acceptance-live.sh
#
# Not part of CI: the key must never be there. The offline suite (test-github-app-token.sh) is.
set -euo pipefail
set +o xtrace
here=$(cd "$(dirname "$0")" && pwd)
minter="$here/../scripts/github-app-token"
[ "${ACCEPT_LIVE:-}" = 1 ] || { echo "set ACCEPT_LIVE=1 to run against the real GitHub API" >&2; exit 2; }

owner=kauki-labs
repo=credential-broker
other=sandy
branch="acceptance/$(date +%s)-$$"
tmp=$(mktemp -d)
n=0
write_token=""
trap 'cleanup' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { n=$((n + 1)); echo "ok $n: $*"; }

# api <token> <method> <path> [json body]: prints the HTTP status, leaves the body in $tmp/body.
# The token goes to curl on stdin, never on argv.
api() {
  local token="$1" method="$2" path="$3" data="${4:-}"
  local args=(-q -sS --proto '=https' --tlsv1.2 --noproxy '*' -o "$tmp/body" -w '%{http_code}'
    -X "$method" -H @- -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28")
  [ -z "$data" ] || args+=(-d "$data")
  printf 'Authorization: Bearer %s\n' "$token" | curl "${args[@]}" "https://api.github.com$path"
}
want() { # want <label> <expected status> <got status>
  [ "$3" = "$2" ] || fail "$1: expected HTTP $2, got $3: $(head -c 300 "$tmp/body")"
  ok "$1"
}
want_denied() { # a refused write is 403 or 404, depending on what GitHub reveals
  case "$2" in 403|404) ok "$1 (HTTP $2)" ;; *) fail "$1: expected 403 or 404, got $2: $(head -c 300 "$tmp/body")" ;; esac
}

cleanup() {
  # Best effort: remove the throwaway branch if a failed run left it behind.
  if [ -n "$write_token" ]; then
    api "$write_token" DELETE "/repos/$owner/$repo/git/refs/heads/$branch" >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp"
}

## A1. A read token for both repos reaches exactly the installed repos -------------------------
read_both=$("$minter" "$repo" "$other")
want "A1 read token lists installation repos" 200 "$(api "$read_both" GET /installation/repositories)"
got=$(jq -r '[.repositories[].name] | sort | join(",")' "$tmp/body")
[ "$got" = "$other,$repo" ] || fail "A1 token reaches [$got], want exactly [$other,$repo]"; ok "A1 token reaches exactly sandy and credential-broker"

## A2. A token narrowed to one repo reaches only that repo --------------------------------------
read_one=$("$minter" "$repo")
want "A2 narrowed token lists repos" 200 "$(api "$read_one" GET /installation/repositories)"
[ "$(jq -r '[.repositories[].name] | join(",")' "$tmp/body")" = "$repo" ] || fail "A2 narrowed token reaches more than $repo"; ok "A2 token reaches only $repo"
want_denied "A2 narrowed token cannot read $other" "$(api "$read_one" GET "/repos/$owner/$other/git/ref/heads/main")"

## A3. A read token reads but cannot write -----------------------------------------------------
want "A3 read token reads the default branch ref" 200 "$(api "$read_one" GET "/repos/$owner/$repo/git/ref/heads/main")"
sha=$(jq -r .object.sha "$tmp/body")
[[ $sha =~ ^[0-9a-f]{40}$ ]] || fail "A3 no commit sha in the ref response"; ok "A3 default branch sha found"
want_denied "A3 read token cannot create a branch" \
  "$(api "$read_one" POST "/repos/$owner/$repo/git/refs" "{\"ref\":\"refs/heads/$branch\",\"sha\":\"$sha\"}")"

## A4. A write token writes to its repo and only its repo --------------------------------------
write_token=$("$minter" --write "$repo")
want "A4 write token creates a branch" 201 \
  "$(api "$write_token" POST "/repos/$owner/$repo/git/refs" "{\"ref\":\"refs/heads/$branch\",\"sha\":\"$sha\"}")"
want "A4 write token deletes the branch" 204 "$(api "$write_token" DELETE "/repos/$owner/$repo/git/refs/heads/$branch")"
want_denied "A4 write token for $repo cannot write to $other" \
  "$(api "$write_token" POST "/repos/$owner/$other/git/refs" "{\"ref\":\"refs/heads/$branch\",\"sha\":\"$sha\"}")"

## A5. The app ceiling: no permission outside contents, pull_requests, issues, metadata -------
want_denied "A5 write token cannot read repository secrets" "$(api "$write_token" GET "/repos/$owner/$repo/actions/secrets")"
want_denied "A5 write token cannot change repository settings" "$(api "$write_token" PATCH "/repos/$owner/$repo" '{"has_wiki":true}')"

## A6. The minter refuses what it should, before any network call ------------------------------
if "$minter" some-other-repo >/dev/null 2>&1; then fail "A6 minter accepted a repo outside the allowlist"; fi
ok "A6 minter refuses a repo outside the allowlist"

## A7. Early revoke kills the token ------------------------------------------------------------
want "A7 token works before revoke" 200 "$(api "$read_one" GET /installation/repositories)"
want "A7 DELETE /installation/token" 204 "$(api "$read_one" DELETE /installation/token)"
want "A7 revoked token is refused" 401 "$(api "$read_one" GET /installation/repositories)"
# The other tokens are separate and still live until their hour ends.
want "A7 revoking one token leaves others alone" 200 "$(api "$read_both" GET /installation/repositories)"

api "$write_token" DELETE /installation/token >/dev/null || true
api "$read_both" DELETE /installation/token >/dev/null || true
write_token=""
echo "live acceptance: $n checks passed"
