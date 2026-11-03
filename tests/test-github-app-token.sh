#!/usr/bin/env bash
# Offline acceptance tests for scripts/github-app-token. Never calls GitHub: a fake `curl` first on PATH
# records argv and the header the script feeds it on stdin, then answers per FAKE_MODE.
# TOOL_UNDER_TEST overrides the script, which the mutation checks use.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
tool="${TOOL_UNDER_TEST:-$here/../scripts/github-app-token}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
n=0
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { n=$((n + 1)); }

openssl genrsa -out "$tmp/key.pem" 2048 2>/dev/null
openssl rsa -in "$tmp/key.pem" -pubout -out "$tmp/pub.pem" 2>/dev/null
openssl genrsa -out "$tmp/other.pem" 2048 2>/dev/null
openssl rsa -in "$tmp/other.pem" -pubout -out "$tmp/other-pub.pem" 2>/dev/null
chmod 600 "$tmp/key.pem"
export GH_APP_ID=Iv1.abc123 GH_APP_KEY_FILE="$tmp/key.pem" GH_APP_INSTALLATION_ID=4242

mkdir "$tmp/bin" "$tmp/tmpdir"
cat > "$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
# Fake curl: record argv, capture stdin when told to read headers from it, answer per mode.
printf '%s\n' "$@" > "$FAKE_DIR/argv"
if printf '%s\n' "$@" | grep -qx -- '@-'; then cat > "$FAKE_DIR/stdin"; fi
touch "$FAKE_DIR/called"
case "${FAKE_MODE:-ok}" in
  ok)         printf '{"token":"ghs_faketoken123","expires_at":"2099-01-01T00:00:00Z"}';;
  http_err)   printf '{"message":"Not Found: repository x is not in the installation"}'; exit 22;;
  no_token)   printf '{"message":"nope"}';;
  null_token) printf '{"token":null}';;
  bad_token)  printf '{"token":"ghs_a b"}';;
  garbage)    printf '<html>oops</html>';;
  empty)      printf '';;
  *) exit 99;;
esac
EOF
chmod +x "$tmp/bin/curl"
export FAKE_DIR="$tmp"

# run <args>: run the tool with the fake curl, a private TMPDIR, and clean capture files
run() { rm -f "$tmp/argv" "$tmp/stdin" "$tmp/called"; PATH="$tmp/bin:$PATH" TMPDIR="$tmp/tmpdir" "$tool" "$@"; }
# must_fail <label> <cmd...>: non-zero exit, nothing on stdout, curl never reached
must_fail() {
  local label="$1"; shift
  local out
  rm -f "$tmp/called"
  if out=$("$@" 2>"$tmp/err"); then fail "$label: exited 0"; fi
  [ -z "$out" ] || fail "$label: wrote to stdout on failure: $out"
  [ ! -e "$tmp/called" ] || fail "$label: reached the network layer"
  ok
}
unb64() { local s="$1"; s=$(printf '%s' "$s" | tr '_-' '/+'); while [ $(( ${#s} % 4 )) -ne 0 ]; do s="$s="; done; printf '%s' "$s" | openssl base64 -d -A; }
jwt_sent() { sed -n 's/^Authorization: Bearer //p' "$tmp/stdin"; }
sent_body() { sed -n '/^-d$/{n;p;}' "$tmp/argv"; }

## 1. Happy path: output, JWT, signature, expiry ---------------------------------------------
before=$(date +%s)
got=$(run sandy credential-broker 2>"$tmp/stderr")
after=$(date +%s)
[ "$got" = "ghs_faketoken123" ] || fail "stdout must be exactly the token, got: $got"; ok
[ ! -s "$tmp/stderr" ] || fail "stderr not empty on success: $(cat "$tmp/stderr")"; ok
[ -z "$(ls -A "$tmp/tmpdir")" ] || fail "script left a file in TMPDIR"; ok

jwt=$(jwt_sent)
[ -n "$jwt" ] || fail "no bearer header reached curl's stdin"; ok
printf '%s' "$jwt" | grep -qE '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$' || fail "JWT is not base64url without padding"; ok
IFS=. read -r h p s <<<"$jwt"
[ "$(unb64 "$h" | jq -r .alg)" = RS256 ] || fail "alg is not RS256"; ok
[ "$(unb64 "$h" | jq -r .typ)" = JWT ] || fail "typ is not JWT"; ok
claims=$(unb64 "$p")
[ "$(printf '%s' "$claims" | jq -r .iss)" = "$GH_APP_ID" ] || fail "iss mismatch"; ok
[ "$(printf '%s' "$claims" | jq 'keys | length')" = 3 ] || fail "unexpected extra claims"; ok
iat=$(printf '%s' "$claims" | jq -r .iat); exp=$(printf '%s' "$claims" | jq -r .exp)
[ "$iat" -lt "$before" ] || fail "iat must be in the past (clock drift allowance)"; ok
[ $((exp - iat)) -eq 600 ] || fail "exp - iat must be exactly 600"; ok
[ "$exp" -le $((after + 540)) ] || fail "exp is more than 9 minutes ahead of now"; ok
[ "$exp" -ge $((before + 540)) ] || fail "exp is too early"; ok
unb64 "$s" > "$tmp/sig.bin"
printf '%s.%s' "$h" "$p" | openssl dgst -sha256 -verify "$tmp/pub.pem" -signature "$tmp/sig.bin" >/dev/null \
  || fail "signature does not verify against the public key"; ok
if printf '%s.%s' "$h" "$p" | openssl dgst -sha256 -verify "$tmp/other-pub.pem" -signature "$tmp/sig.bin" >/dev/null 2>&1; then
  fail "signature verified under an unrelated key"
fi; ok

## 2. What curl receives: flags, URL, secrecy, body -------------------------------------------
[ "$(sed -n 1p "$tmp/argv")" = "-q" ] || fail "curl -q must be the first argument (skips ~/.curlrc)"; ok
grep -qx -- '--noproxy' "$tmp/argv" && grep -qx '\*' "$tmp/argv" || fail "proxies not disabled"; ok
grep -qx -- '--proto' "$tmp/argv" && grep -qx '=https' "$tmp/argv" || fail "protocol not pinned to https"; ok
grep -qx -- '--tlsv1.2' "$tmp/argv" || fail "TLS floor missing"; ok
for bad in -k --insecure --proxy -x --cacert --capath --proxy-insecure -L --location --trace --trace-ascii -v --verbose; do
  if grep -qx -- "$bad" "$tmp/argv"; then fail "curl argument $bad must not be used"; fi
done; ok
grep -qx 'https://api.github.com/app/installations/4242/access_tokens' "$tmp/argv" || fail "wrong URL"; ok
grep -qx -- '-X' "$tmp/argv" && grep -qx 'POST' "$tmp/argv" || fail "not a POST"; ok
if grep -qF "$jwt" "$tmp/argv"; then fail "JWT leaked to argv"; fi; ok
if grep -qF "ghs_faketoken123" "$tmp/argv"; then fail "token on argv"; fi; ok

# the exact body: nothing missing, nothing extra
[ "$(sent_body | jq -cS .)" = '{"permissions":{"contents":"read","issues":"read","metadata":"read","pull_requests":"read"},"repositories":["sandy","credential-broker"]}' ] \
  || fail "read body differs from the expected exact body: $(sent_body)"; ok
run --write sandy >/dev/null
[ "$(sent_body | jq -cS .)" = '{"permissions":{"contents":"write","issues":"write","metadata":"read","pull_requests":"write"},"repositories":["sandy"]}' ] \
  || fail "write body differs from the expected exact body: $(sent_body)"; ok
run -- sandy >/dev/null
[ "$(sent_body | jq -c .repositories)" = '["sandy"]' ] || fail "-- handling broke the repo list"; ok

# a hostile environment cannot widen, redirect or leak
out=$(GH_APP_DRY_RUN=1 GH_APP_PERMISSIONS='{"contents":"write"}' PERMISSIONS=contents:write run sandy)
[ "$out" = "ghs_faketoken123" ] || fail "GH_APP_DRY_RUN must not change the output (JWT would leak): $out"; ok
[ -e "$tmp/called" ] || fail "GH_APP_DRY_RUN skipped the real request path"; ok
[ "$(sent_body | jq -c '[.permissions[]] | unique')" = '["read"]' ] || fail "environment widened permissions"; ok

## 3. Allowlist, arguments, ids ---------------------------------------------------------------
must_fail "repo outside the allowlist" run other-repo
must_fail "one bad repo among good ones" run sandy other-repo
must_fail "allowlist is exact, not prefix" run sandy-extra
must_fail "json breakout repo" run 'a","permissions":{"contents":"write"},"x":"'
must_fail "repo with slash (owner/name)" run kauki-labs/sandy
must_fail "repo with space" run 'my repo'
must_fail "repo with newline" run $'sandy\nevil'
must_fail "repo dot-dot" run '..'
must_fail "empty repo name" run ''
must_fail "option-like repo" run '-x'
must_fail "long option" run --yolo sandy
must_fail "write flag but no repo" run --write
must_fail "no repositories" run
must_fail "installation id path traversal" env GH_APP_INSTALLATION_ID='1/../../../orgs/x' PATH="$tmp/bin:$PATH" "$tool" sandy
must_fail "installation id with query" env GH_APP_INSTALLATION_ID='1?x=y' PATH="$tmp/bin:$PATH" "$tool" sandy
must_fail "installation id non-numeric" env GH_APP_INSTALLATION_ID=abc PATH="$tmp/bin:$PATH" "$tool" sandy
must_fail "installation id empty" env GH_APP_INSTALLATION_ID= PATH="$tmp/bin:$PATH" "$tool" sandy
must_fail "app id with quote" env GH_APP_ID='x","admin":"1' PATH="$tmp/bin:$PATH" "$tool" sandy
must_fail "app id empty" env GH_APP_ID= PATH="$tmp/bin:$PATH" "$tool" sandy
must_fail "missing app id" env -u GH_APP_ID PATH="$tmp/bin:$PATH" "$tool" sandy
must_fail "missing key file var" env -u GH_APP_KEY_FILE PATH="$tmp/bin:$PATH" "$tool" sandy
must_fail "missing installation var" env -u GH_APP_INSTALLATION_ID PATH="$tmp/bin:$PATH" "$tool" sandy

## 4. Key file handling -----------------------------------------------------------------------
for m in 644 640 660 604 666 700 770 777 000; do
  chmod "$m" "$tmp/key.pem"
  must_fail "key mode $m" run sandy
done
chmod 400 "$tmp/key.pem"; run sandy >/dev/null || fail "mode 400 must be accepted"; ok
chmod 600 "$tmp/key.pem"
ln -s "$tmp/key.pem" "$tmp/link.pem"
GH_APP_KEY_FILE="$tmp/link.pem" run sandy >/dev/null || fail "symlink to a 0600 key must be accepted"; ok
cp "$tmp/key.pem" "$tmp/loose.pem"; chmod 644 "$tmp/loose.pem"; ln -s "$tmp/loose.pem" "$tmp/looselink.pem"
must_fail "symlink to a 0644 key" env GH_APP_KEY_FILE="$tmp/looselink.pem" PATH="$tmp/bin:$PATH" "$tool" sandy
must_fail "key file missing" env GH_APP_KEY_FILE="$tmp/nope.pem" PATH="$tmp/bin:$PATH" "$tool" sandy
: > "$tmp/empty.pem"; chmod 600 "$tmp/empty.pem"
must_fail "empty key file" env GH_APP_KEY_FILE="$tmp/empty.pem" PATH="$tmp/bin:$PATH" "$tool" sandy
printf 'not a pem\n' > "$tmp/junk.pem"; chmod 600 "$tmp/junk.pem"
must_fail "junk key file" env GH_APP_KEY_FILE="$tmp/junk.pem" PATH="$tmp/bin:$PATH" "$tool" sandy
must_fail "key path is a directory" env GH_APP_KEY_FILE="$tmp" PATH="$tmp/bin:$PATH" "$tool" sandy
cp "$tmp/pub.pem" "$tmp/pubonly.pem"; chmod 600 "$tmp/pubonly.pem"
must_fail "public key as signing key" env GH_APP_KEY_FILE="$tmp/pubonly.pem" PATH="$tmp/bin:$PATH" "$tool" sandy

## 5. Upstream failures and leak checks -----------------------------------------------------
for mode in no_token null_token bad_token garbage empty; do
  out=$(FAKE_MODE=$mode run sandy 2>"$tmp/e") && fail "mode $mode: exited 0"
  [ -z "$out" ] || fail "mode $mode: printed something on failure: $out"
  [ -z "$(ls -A "$tmp/tmpdir")" ] || fail "mode $mode: file left in TMPDIR"
  if grep -qF 'ghs_a' "$tmp/e"; then fail "mode $mode: a malformed token was echoed to stderr"; fi
  ok
done
out=$(FAKE_MODE=http_err run sandy 2>"$tmp/e") && fail "http error: exited 0"
[ -z "$out" ] || fail "http error: printed on stdout"; ok
grep -q 'not in the installation' "$tmp/e" || fail "GitHub's error body must reach stderr for the operator"; ok
if grep -qF "$(jwt_sent)" "$tmp/e"; then fail "JWT printed on failure"; fi; ok
if grep -q 'BEGIN' "$tmp/e"; then fail "key material printed on failure"; fi; ok

# tracing must not leak the JWT, even with an attacker-chosen trace fd
exec 7>"$tmp/trace"
env FAKE_MODE=ok SHELLOPTS=xtrace PS4='+X ' BASH_XTRACEFD=7 PATH="$tmp/bin:$PATH" TMPDIR="$tmp/tmpdir" "$tool" sandy >"$tmp/o" 2>"$tmp/e" \
  || fail "run under SHELLOPTS=xtrace failed"
exec 7>&-
[ "$(cat "$tmp/o")" = "ghs_faketoken123" ] || fail "token wrong under SHELLOPTS=xtrace"; ok
if grep -q 'Bearer\|[A-Za-z0-9_-]\{40,\}' "$tmp/e" "$tmp/trace"; then fail "xtrace leaked the JWT"; fi; ok

echo "ok: github-app-token ($n checks)"
