#!/usr/bin/env bash
# Offline acceptance tests for scripts/github-app-webhook. Never calls GitHub: a fake `curl` first
# on PATH records argv, the stdin header and the body file, then answers per FAKE_MODE.
# TOOL_UNDER_TEST overrides the script, which mutation checks use.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
tool="${TOOL_UNDER_TEST:-$here/../scripts/github-app-webhook}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
n=0
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { n=$((n + 1)); }

openssl genrsa -out "$tmp/key.pem" 2048 2>/dev/null
openssl rsa -in "$tmp/key.pem" -pubout -out "$tmp/pub.pem" 2>/dev/null
chmod 600 "$tmp/key.pem"
secret='s3cr3t-webhook-value-0123456789'
printf '%s\n' "$secret" > "$tmp/secret"; chmod 600 "$tmp/secret"
export GH_APP_ID=Iv1.abc123 GH_APP_KEY_FILE="$tmp/key.pem" GH_APP_WEBHOOK_SECRET_FILE="$tmp/secret"

mkdir "$tmp/bin" "$tmp/tmpdir"
cat > "$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
# Fake curl: record argv, the stdin header, and the body file's content at call time.
printf '%s\n' "$@" > "$FAKE_DIR/argv"
if printf '%s\n' "$@" | grep -qx -- '@-'; then cat > "$FAKE_DIR/stdin"; fi
body=$(printf '%s\n' "$@" | sed -n 's/^@//p' | grep -v '^-$' | head -1 || true)
if [ -n "$body" ]; then cat "$body" > "$FAKE_DIR/body"; stat -L -c '%a' "$body" > "$FAKE_DIR/bodymode" 2>/dev/null || stat -L -f '%Lp' "$body" > "$FAKE_DIR/bodymode"; fi
touch "$FAKE_DIR/called"
case "${FAKE_MODE:-ok}" in
  ok)       printf '{"url":"%s","content_type":"json","secret":"********","insecure_ssl":"0"}' "${FAKE_URL:-http://localhost:8080/}";;
  http_err) printf '{"message":"Bad credentials"}'; exit 22;;
  no_url)   printf '{"message":"nope"}';;
  garbage)  printf '<html>oops</html>';;
  *) exit 99;;
esac
EOF
chmod +x "$tmp/bin/curl"
export FAKE_DIR="$tmp"

run() { rm -f "$tmp/argv" "$tmp/stdin" "$tmp/body" "$tmp/bodymode" "$tmp/called"; PATH="$tmp/bin:$PATH" TMPDIR="$tmp/tmpdir" "$tool" "$@"; }
must_fail() { # must_fail <label> <cmd...>: non-zero exit, nothing on stdout, curl never reached
  local label="$1"; shift
  local out
  rm -f "$tmp/called"
  if out=$("$@" 2>"$tmp/err"); then fail "$label: exited 0"; fi
  [ -z "$out" ] || fail "$label: wrote to stdout on failure: $out"
  [ ! -e "$tmp/called" ] || fail "$label: reached the network layer"
  if grep -qF "$secret" "$tmp/err"; then fail "$label: the secret reached stderr"; fi
  ok
}
unb64() { local s="$1"; s=$(printf '%s' "$s" | tr '_-' '/+'); while [ $(( ${#s} % 4 )) -ne 0 ]; do s="$s="; done; printf '%s' "$s" | openssl base64 -d -A; }
jwt_sent() { sed -n 's/^Authorization: Bearer //p' "$tmp/stdin"; }

## 1. set: output, JWT, request ----------------------------------------------------------------
url=https://hooks.example.org/github
got=$(FAKE_URL=$url run set "$url" 2>"$tmp/stderr")
[ "$got" = "$url" ] || fail "stdout must be the URL GitHub holds, got: $got"; ok
[ ! -s "$tmp/stderr" ] || fail "stderr not empty for a public URL: $(cat "$tmp/stderr")"; ok
[ -z "$(ls -A "$tmp/tmpdir")" ] || fail "script left a file in TMPDIR"; ok

jwt=$(jwt_sent)
IFS=. read -r h p s <<<"$jwt"
[ "$(unb64 "$h" | jq -r .alg)" = RS256 ] || fail "alg is not RS256"; ok
claims=$(unb64 "$p")
[ "$(printf '%s' "$claims" | jq -r .iss)" = "$GH_APP_ID" ] || fail "iss mismatch"; ok
[ $(( $(printf '%s' "$claims" | jq -r .exp) - $(printf '%s' "$claims" | jq -r .iat) )) -eq 600 ] || fail "exp - iat must be 600"; ok
unb64 "$s" > "$tmp/sig.bin"
printf '%s.%s' "$h" "$p" | openssl dgst -sha256 -verify "$tmp/pub.pem" -signature "$tmp/sig.bin" >/dev/null || fail "bad JWT signature"; ok

[ "$(sed -n 1p "$tmp/argv")" = "-q" ] || fail "curl -q must be the first argument"; ok
grep -qx -- '--noproxy' "$tmp/argv" || fail "proxies not disabled"; ok
grep -qx -- '--tlsv1.2' "$tmp/argv" || fail "TLS floor missing"; ok
grep -qx -- '--proto' "$tmp/argv" || fail "protocol not pinned"; ok
grep -qx -- '-X' "$tmp/argv" && grep -qx 'PATCH' "$tmp/argv" || fail "set must PATCH"; ok
grep -qx 'https://api.github.com/app/hook/config' "$tmp/argv" || fail "wrong URL"; ok
for bad in -k --insecure --proxy -L --location -v --verbose --trace; do
  if grep -qx -- "$bad" "$tmp/argv"; then fail "curl argument $bad must not be used"; fi
done; ok
if grep -qF "$jwt" "$tmp/argv"; then fail "JWT leaked to argv"; fi; ok
if grep -qF "$secret" "$tmp/argv"; then fail "secret leaked to argv"; fi; ok
[ "$(jq -cS . "$tmp/body")" = "{\"content_type\":\"json\",\"insecure_ssl\":\"0\",\"secret\":\"$secret\",\"url\":\"$url\"}" ] \
  || fail "request body differs from the expected exact body"; ok
[ "$(cat "$tmp/bodymode")" = 600 ] || fail "body file was not mode 600"; ok
if grep -qF "$secret" "$tmp/stderr" "$tmp/stdin"; then fail "secret reached stderr or the stdin header"; fi; ok

# a localhost URL works but warns, and still prints only the URL
got=$(run set http://localhost:8080/ 2>"$tmp/stderr")
[ "$got" = "http://localhost:8080/" ] || fail "localhost URL output: $got"; ok
grep -q 'cannot reach a localhost URL' "$tmp/stderr" || fail "no localhost warning"; ok

## 2. show -------------------------------------------------------------------------------------
got=$(run show 2>"$tmp/stderr")
[ "$got" = '{"url":"http://localhost:8080/","content_type":"json","insecure_ssl":"0"}' ] || fail "show output: $got"; ok
if printf '%s' "$got" | grep -q 'secret\|\*\*\*'; then fail "show printed the secret field"; fi; ok
grep -qx 'GET' "$tmp/argv" || fail "show must GET"; ok
[ ! -e "$tmp/body" ] || fail "show must not send a body"; ok
run show >/dev/null 2>&1; GH_APP_WEBHOOK_SECRET_FILE=/nonexistent run show >/dev/null || fail "show must not need the secret file"; ok

## 3. Arguments, URLs, secrets, keys ---------------------------------------------------------
must_fail "no subcommand" run
must_fail "unknown subcommand" run delete
must_fail "set without a url" run set
must_fail "set with extra arguments" run set https://a.example/x https://b.example/y
must_fail "show with an argument" run show extra
must_fail "url without a scheme" run set hooks.example.org/x
must_fail "ftp url" run set ftp://hooks.example.org/x
must_fail "url with a space" run set 'https://a.example/x y'
must_fail "url with a quote (json breakout)" run set 'https://a.example/","x":"'
must_fail "url with a backslash" run set 'https://a.example/\x'
must_fail "url with a newline" run set $'https://a.example/x\ny'
must_fail "url starting with a dash" run set -- '-d'
must_fail "empty url" run set ''

short="$tmp/short"; printf 'tooshort\n' > "$short"; chmod 600 "$short"
must_fail "secret under 16 characters" env GH_APP_WEBHOOK_SECRET_FILE="$short" PATH="$tmp/bin:$PATH" "$tool" set "$url"
two="$tmp/two"; printf '%s\nsecondline-of-secret-0123\n' "$secret" > "$two"; chmod 600 "$two"
must_fail "secret file with two lines" env GH_APP_WEBHOOK_SECRET_FILE="$two" PATH="$tmp/bin:$PATH" "$tool" set "$url"
sp="$tmp/space"; printf 'has a space in it 0123456789\n' > "$sp"; chmod 600 "$sp"
must_fail "secret with spaces" env GH_APP_WEBHOOK_SECRET_FILE="$sp" PATH="$tmp/bin:$PATH" "$tool" set "$url"
empty="$tmp/empty"; : > "$empty"; chmod 600 "$empty"
must_fail "empty secret file" env GH_APP_WEBHOOK_SECRET_FILE="$empty" PATH="$tmp/bin:$PATH" "$tool" set "$url"
loose="$tmp/loose"; cp "$tmp/secret" "$loose"; chmod 644 "$loose"
must_fail "secret file mode 644" env GH_APP_WEBHOOK_SECRET_FILE="$loose" PATH="$tmp/bin:$PATH" "$tool" set "$url"
must_fail "missing secret file" env GH_APP_WEBHOOK_SECRET_FILE="$tmp/none" PATH="$tmp/bin:$PATH" "$tool" set "$url"
must_fail "secret env unset" env -u GH_APP_WEBHOOK_SECRET_FILE PATH="$tmp/bin:$PATH" "$tool" set "$url"
cp "$tmp/key.pem" "$tmp/loosekey.pem"; chmod 644 "$tmp/loosekey.pem"
must_fail "key mode 644" env GH_APP_KEY_FILE="$tmp/loosekey.pem" PATH="$tmp/bin:$PATH" "$tool" show
must_fail "missing key file" env GH_APP_KEY_FILE="$tmp/none.pem" PATH="$tmp/bin:$PATH" "$tool" show
printf 'not a key\n' > "$tmp/bad.pem"; chmod 600 "$tmp/bad.pem"
must_fail "key file that is not a key" env GH_APP_KEY_FILE="$tmp/bad.pem" PATH="$tmp/bin:$PATH" "$tool" show
must_fail "GH_APP_ID unset" env -u GH_APP_ID PATH="$tmp/bin:$PATH" "$tool" show
must_fail "GH_APP_ID with a quote" env GH_APP_ID='a"b' PATH="$tmp/bin:$PATH" "$tool" show
ln -s "$tmp/key.pem" "$tmp/link.pem"
env GH_APP_KEY_FILE="$tmp/link.pem" PATH="$tmp/bin:$PATH" "$tool" show >/dev/null || fail "a symlink to a 0600 key must work"; ok

## 4. Upstream failures: fail closed, no secret in any output ------------------------------
for mode in http_err no_url garbage; do
  rm -f "$tmp/called"
  if out=$(FAKE_MODE=$mode run set "$url" 2>"$tmp/err"); then fail "$mode: exited 0"; fi
  [ -z "$out" ] || fail "$mode: wrote to stdout: $out"
  if grep -qF "$secret" "$tmp/err"; then fail "$mode: secret on stderr"; fi
  [ -z "$(ls -A "$tmp/tmpdir")" ] || fail "$mode: left a file in TMPDIR"
  ok
done
rm -f "$tmp/called"
if out=$(FAKE_MODE=garbage run show 2>/dev/null); then fail "show accepted a garbage response"; fi
[ -z "$out" ] || fail "show wrote to stdout on garbage"; ok

## 5. Hostile environment -------------------------------------------------------------------
rm -f "$tmp/argv" "$tmp/stdin" "$tmp/body" "$tmp/bodymode" "$tmp/called"
env SHELLOPTS=xtrace BASH_XTRACEFD=2 FAKE_URL="$url" PATH="$tmp/bin:$PATH" TMPDIR="$tmp/tmpdir" "$tool" set "$url" >"$tmp/out" 2>"$tmp/trace" \
  || fail "the hostile-environment run failed: $(head -c 300 "$tmp/trace")"
[ -e "$tmp/called" ] || fail "the hostile-environment run never reached curl"
[ "$(cat "$tmp/out")" = "$url" ] || fail "the hostile-environment run changed the output"
if grep -qF "$secret" "$tmp/trace" || grep -qF "$(jwt_sent)" "$tmp/trace"; then fail "xtrace leaked the secret or the JWT"; fi; ok
[ -z "$(ls -A "$tmp/tmpdir")" ] || fail "TMPDIR not clean after the hostile run"; ok

echo "ok: github-app-webhook ($n checks)"
