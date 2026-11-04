#!/usr/bin/env bash
# Live acceptance tests for the `external` GitHub App, run by the operator after registering it.
# Talks to the real api.github.com with real tokens from scripts/github-app-token and with the
# App's own JWT. It touches one throwaway branch on credential-broker and deletes it again.
#
#   ACCEPT_LIVE=1 GH_APP_ID=... GH_APP_KEY_FILE=... GH_APP_INSTALLATION_ID=... tests/acceptance-live.sh
#
# Not part of CI: the key must never be there. The offline suite (test-github-app-token.sh) is.
set -euo pipefail
set +o xtrace
here=$(cd "$(dirname "$0")" && pwd)
minter="$here/../scripts/github-app-token"
[ "${ACCEPT_LIVE:-}" = 1 ] || { echo "set ACCEPT_LIVE=1 to run against the real GitHub API" >&2; exit 2; }
: "${GH_APP_ID:?set GH_APP_ID}" "${GH_APP_KEY_FILE:?set GH_APP_KEY_FILE}" "${GH_APP_INSTALLATION_ID:?set GH_APP_INSTALLATION_ID}"

owner=kauki-labs
repo=credential-broker
other=sandy
branch="acceptance/$(date +%s)-$$"
tmp=$(mktemp -d)
n=0
read_both="" read_one="" write_token=""
trap cleanup EXIT
# fail also runs inside $(field ...): a plain exit there would end only the subshell and the suite would go on.
fail() { echo "FAIL: $*" >&2; [ "$BASHPID" = "$$" ] || kill -s TERM "$$"; exit 1; }
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
field() { jq -er "$1" "$tmp/body" || fail "unexpected response body: $(head -c 300 "$tmp/body")"; }

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
app_jwt() { # the App's own JWT, for the checks that read the App and its installation
  local now si
  now=$(date +%s)
  si="$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | b64url).$(jq -cn --arg iss "$GH_APP_ID" --argjson iat "$((now - 60))" --argjson exp "$((now + 540))" '{iat:$iat,exp:$exp,iss:$iss}' | tr -d '\n' | b64url)"
  printf '%s.%s' "$si" "$(printf '%s' "$si" | openssl dgst -sha256 -sign "$GH_APP_KEY_FILE" | b64url)"
}

cleanup() {
  # Best effort, on success and on failure: delete the throwaway branch, then revoke every token.
  local t
  if [ -n "$write_token" ]; then
    api "$write_token" DELETE "/repos/$owner/$repo/git/refs/heads/$branch" >/dev/null 2>&1 || true
  fi
  for t in "$read_both" "$read_one" "$write_token"; do
    [ -z "$t" ] || api "$t" DELETE /installation/token >/dev/null 2>&1 || true
  done
  rm -rf "$tmp"
}

read_both=$("$minter" "$repo" "$other")
read_one=$("$minter" "$repo")
write_token=$("$minter" --write "$repo")   # minted early so cleanup can always remove the branch

## A1. The installation is limited to selected repos, and a token reaches exactly those --------
jwt=$(app_jwt)
want "A1 read the installation as the App" 200 "$(api "$jwt" GET "/app/installations/$GH_APP_INSTALLATION_ID")"
[ "$(field .repository_selection)" = selected ] || fail "A1 installation is not limited to selected repositories"; ok "A1 installation is 'Only select repositories'"
want "A1 token lists installation repos" 200 "$(api "$read_both" GET /installation/repositories)"
[ "$(field '[.repositories[].name] | sort | join(",")')" = "$repo,$other" ] || fail "A1 installation reaches repos other than $other and $repo"; ok "A1 installation reaches exactly sandy and credential-broker"

## A2. The App ceiling is exactly the documented permission set -------------------------------
want "A2 read the App permissions" 200 "$(api "$jwt" GET "/app/installations/$GH_APP_INSTALLATION_ID")"
[ "$(field '.permissions | to_entries | sort_by(.key) | map("\(.key)=\(.value)") | join(",")')" = "contents=write,issues=write,metadata=read,pull_requests=write" ] \
  || fail "A2 App permissions differ from the ceiling: $(jq -c .permissions "$tmp/body")"; ok "A2 permissions are exactly contents, issues, pull_requests write and metadata read"

## A3. A token narrowed to one repo reaches only that repo -------------------------------------
want "A3 narrowed token lists repos" 200 "$(api "$read_one" GET /installation/repositories)"
[ "$(field '[.repositories[].name] | join(",")')" = "$repo" ] || fail "A3 narrowed token reaches more than $repo"; ok "A3 token reaches only $repo"
want "A3 look up $other" 200 "$(api "$read_both" GET "/repos/$owner/$other")"
if [ "$(field '.private | tostring')" = true ]; then
  want "A3 narrowed token cannot see private $other" 404 "$(api "$read_one" GET "/repos/$owner/$other")"
else
  echo "skip: $other is public, so a narrowed token can read it; the A5 write check covers separation"
fi

## A4. A read token reads but cannot write -----------------------------------------------------
want "A4 read token reads the default branch ref" 200 "$(api "$read_one" GET "/repos/$owner/$repo/git/ref/heads/main")"
sha=$(field .object.sha)
[[ $sha =~ ^[0-9a-f]{40}$ ]] || fail "A4 no commit sha in the ref response"; ok "A4 default branch sha found"
want_denied "A4 read token cannot create a branch" \
  "$(api "$read_one" POST "/repos/$owner/$repo/git/refs" "{\"ref\":\"refs/heads/$branch\",\"sha\":\"$sha\"}")"

## A5. A write token writes to its repo and only its repo --------------------------------------
want "A5 write token creates a branch" 201 \
  "$(api "$write_token" POST "/repos/$owner/$repo/git/refs" "{\"ref\":\"refs/heads/$branch\",\"sha\":\"$sha\"}")"
want "A5 write token deletes the branch" 204 "$(api "$write_token" DELETE "/repos/$owner/$repo/git/refs/heads/$branch")"
want "A5 look up $other" 200 "$(api "$read_both" GET "/repos/$owner/$other")"
other_branch=$(field .default_branch)
want "A5 read the $other default branch" 200 "$(api "$read_both" GET "/repos/$owner/$other/git/ref/heads/$other_branch")"
other_sha=$(field .object.sha)
want_denied "A5 write token for $repo cannot write to $other" \
  "$(api "$write_token" POST "/repos/$owner/$other/git/refs" "{\"ref\":\"refs/heads/$branch\",\"sha\":\"$other_sha\"}")"

## A6. The token carries no permission outside the ceiling ------------------------------------
want_denied "A6 write token cannot read repository secrets" "$(api "$write_token" GET "/repos/$owner/$repo/actions/secrets")"
want_denied "A6 write token cannot change repository settings" "$(api "$write_token" PATCH "/repos/$owner/$repo" '{"has_wiki":true}')"

## A7. The minter refuses a repo outside its allowlist, with the allowlist error --------------
rc=0; "$minter" some-other-repo >/dev/null 2>"$tmp/err" || rc=$?
[ "$rc" = 2 ] || fail "A7 minter exit code $rc, want 2"
grep -q 'repository not in the allowlist' "$tmp/err" || fail "A7 minter refused for another reason: $(cat "$tmp/err")"
ok "A7 minter refuses a repo outside the allowlist"

## A8. Early revoke kills the token and only that token ----------------------------------------
want "A8 token works before revoke" 200 "$(api "$read_one" GET /installation/repositories)"
want "A8 DELETE /installation/token" 204 "$(api "$read_one" DELETE /installation/token)"
status=000
for _ in 1 2 3 4 5; do
  status=$(api "$read_one" GET /installation/repositories)
  [ "$status" = 401 ] && break
  sleep 1
done
want "A8 revoked token is refused" 401 "$status"
read_one=""
want "A8 revoking one token leaves others alone" 200 "$(api "$read_both" GET /installation/repositories)"

## A9. Every token is revoked at the end, and the revoke is checked ---------------------------
want "A9 revoke the write token" 204 "$(api "$write_token" DELETE /installation/token)"
write_token=""
want "A9 revoke the shared read token" 204 "$(api "$read_both" DELETE /installation/token)"
read_both=""
echo "live acceptance: $n checks passed"
