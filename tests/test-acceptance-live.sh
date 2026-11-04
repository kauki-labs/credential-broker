#!/usr/bin/env bash
# Offline tests for tests/acceptance-live.sh itself: a fake GitHub behind a fake `curl` runs the
# live suite. The honest fake must pass; each broken fake (a wrong install, an over-wide App, a
# read token that can write, a failing revoke) must make the suite fail, so it cannot pass
# vacuously against the real API.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
suite="${SUITE_UNDER_TEST:-$here/acceptance-live.sh}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

openssl genrsa -out "$tmp/key.pem" 2048 2>/dev/null
chmod 600 "$tmp/key.pem"
export ACCEPT_LIVE=1 GH_APP_ID=Iv1.abc123 GH_APP_KEY_FILE="$tmp/key.pem" GH_APP_INSTALLATION_ID=4242
export FAKE_DIR="$tmp/fake"
mkdir -p "$tmp/bin" "$FAKE_DIR"

cat > "$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
# Fake GitHub. FAKE_GH picks one misbehaviour; unset means an honest GitHub.
method=GET; out=""; data=""; url=""; stdin_hdr=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in
    -X) method=${args[i+1]} ;;
    -o) out=${args[i+1]} ;;
    -d) data=${args[i+1]} ;;
    @-) stdin_hdr=$(cat) ;;
    https://*) url=${args[i]} ;;
  esac
done
path=${url#https://api.github.com}
token=$(printf '%s' "$stdin_hdr" | sed -n 's/^Authorization: Bearer //p')
mkdir -p "$FAKE_DIR/revoked"
reply() { # reply <status> [json]
  if [ -n "$out" ]; then printf '%s' "${2:-{\}}" > "$out"; printf '%s' "$1"; else printf '%s' "${2:-{\}}"; [ "$1" -lt 400 ] || exit 22; fi
  exit 0
}
sha=0123456789abcdef0123456789abcdef01234567

# Minting: the token name records the profile the minter asked for.
if [ "$method" = POST ] && [[ $path == /app/installations/*/access_tokens ]]; then
  if [ "$(jq -r .permissions.contents <<<"$data")" = write ]; then t=ghs_write
  elif [ "$(jq '.repositories | length' <<<"$data")" = 2 ]; then t=ghs_readboth
  else t=ghs_readone; fi
  reply 201 "{\"token\":\"$t\"}"
fi
[ ! -e "$FAKE_DIR/revoked/$token" ] || reply 401 '{"message":"Bad credentials"}'

perms='{"contents":"write","issues":"write","metadata":"read","pull_requests":"write"}'
[ "${FAKE_GH:-}" != admin_perm ] || perms='{"administration":"write","contents":"write","issues":"write","metadata":"read","pull_requests":"write"}'
sel=selected; [ "${FAKE_GH:-}" != sel_all ] || sel=all

case "$method $path" in
  "GET /app/installations/4242") reply 200 "{\"repository_selection\":\"$sel\",\"permissions\":$perms}" ;;
  "GET /installation/repositories")
    if [ "$token" = ghs_readboth ]; then reply 200 '{"repositories":[{"name":"sandy"},{"name":"credential-broker"}]}'; fi
    reply 200 '{"repositories":[{"name":"credential-broker"}]}' ;;
  "DELETE /installation/token")
    [ "${FAKE_GH:-}" != revoke_fail ] || reply 403 '{"message":"nope"}'
    touch "$FAKE_DIR/revoked/$token"; reply 204 ;;
  "GET /repos/kauki-labs/sandy")
    [ "$token" = ghs_readboth ] || reply 404 '{"message":"Not Found"}'
    priv=true; [ "${FAKE_GH:-}" != public_other ] || priv=false   # jq -e fails on false, so cover it
    reply 200 "{\"private\":$priv,\"default_branch\":\"main\"}" ;;
  "GET /repos/kauki-labs/credential-broker/git/ref/heads/main" | "GET /repos/kauki-labs/sandy/git/ref/heads/main")
    reply 200 "{\"object\":{\"sha\":\"$sha\"}}" ;;
  "POST /repos/kauki-labs/credential-broker/git/refs")
    if [ "$token" = ghs_write ] || [ "${FAKE_GH:-}" = read_writes ]; then touch "$FAKE_DIR/branch"; reply 201; fi
    reply 403 '{"message":"Resource not accessible by integration"}' ;;
  "DELETE /repos/kauki-labs/credential-broker/git/refs/heads/"*)
    [ "$token" = ghs_write ] || reply 403 '{"message":"Resource not accessible by integration"}'
    rm -f "$FAKE_DIR/branch"; reply 204 ;;
  "POST /repos/kauki-labs/sandy/git/refs") reply 404 '{"message":"Not Found"}' ;;
  "GET /repos/kauki-labs/credential-broker/actions/secrets") reply 403 '{"message":"denied"}' ;;
  "PATCH /repos/kauki-labs/credential-broker") reply 403 '{"message":"denied"}' ;;
esac
reply 599 "{\"message\":\"fake has no route for $method $path\"}"
EOF
chmod +x "$tmp/bin/curl"
printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/sleep"; chmod +x "$tmp/bin/sleep"

run() { rm -rf "$FAKE_DIR"; mkdir -p "$FAKE_DIR"; PATH="$tmp/bin:$PATH" FAKE_GH="${1:-}" "$suite" >"$tmp/out" 2>"$tmp/err"; }

n=0
run "" || fail "honest GitHub: the suite failed: $(cat "$tmp/err")"
grep -q 'live acceptance: .* checks passed' "$tmp/out" || fail "honest GitHub: no success line"; n=$((n + 1))
[ ! -e "$FAKE_DIR/branch" ] || fail "honest GitHub: the suite left the branch behind"; n=$((n + 1))
[ "$(find "$FAKE_DIR/revoked" -type f | wc -l | tr -d ' ')" = 3 ] || fail "honest GitHub: not all three tokens were revoked"; n=$((n + 1))
if grep -q 'ghs_' "$tmp/out" "$tmp/err"; then fail "a token reached the suite's output"; fi; n=$((n + 1))

# a public sibling repo is the real-world case: A3 must skip, not fail
run public_other || fail "public_other: the suite failed: $(cat "$tmp/err")"
grep -q '^skip: sandy is public' "$tmp/out" || fail "public_other: no skip line"; n=$((n + 1))
! grep -q 'FAIL' "$tmp/err" || fail "public_other: a FAIL line was printed yet the suite went on: $(cat "$tmp/err")"; n=$((n + 1))

for mode in sel_all admin_perm read_writes revoke_fail; do
  if run "$mode"; then fail "$mode: the suite passed against a broken GitHub"; fi
  grep -q '^FAIL:' "$tmp/err" || fail "$mode: failed without a FAIL line: $(cat "$tmp/err")"
  n=$((n + 1))
done
# a read token that can write must not leave its branch behind, and every token still gets revoked
run read_writes || true
[ ! -e "$FAKE_DIR/branch" ] || fail "read_writes: the branch was left behind"; n=$((n + 1))
[ "$(find "$FAKE_DIR/revoked" -type f | wc -l | tr -d ' ')" = 3 ] || fail "read_writes: tokens left live after a failure"; n=$((n + 1))

echo "ok: acceptance-live ($n checks)"
