# shellcheck shell=bash
# Shared by github-app-token and github-app-webhook. Sourced, never run: it sets no shell options.
# The caller has `set -euo pipefail` and exports GH_APP_ID and GH_APP_KEY_FILE.

# require_private_file <path> <label>: exit 1 unless the file exists with mode 600 or 400.
require_private_file() {
  local m
  [ -f "$1" ] || { echo "$2 not found: $1" >&2; exit 1; }
  # GNU stat first: on a nix-provided GNU stat, `-f` means filesystem status, not format.
  # -L follows a symlink so a link to a 0600 file (sops, home-manager) is judged by its target.
  m=$(stat -L -c '%a' "$1" 2>/dev/null || stat -L -f '%Lp' "$1")
  case "$m" in 400|600) ;; *) echo "refusing: $1 has mode $m, want 600 or 400" >&2; exit 1 ;; esac
}

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# app_jwt: print an RS256 JWT for the App. Run it as `jwt=$(app_jwt)` so a failure stops the caller.
app_jwt() {
  local now payload input sig
  now=$(date +%s)
  # iat 60 s in the past for clock drift, exp inside GitHub's 10 minute maximum.
  payload=$(jq -cn --arg iss "$GH_APP_ID" --argjson iat "$((now - 60))" --argjson exp "$((now + 540))" \
    '{iat: $iat, exp: $exp, iss: $iss}') || return 1
  input="$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | b64url).$(printf '%s' "$payload" | b64url)" || return 1
  sig=$(printf '%s' "$input" | openssl dgst -sha256 -sign "$GH_APP_KEY_FILE" | b64url) || return 1
  printf '%s.%s' "$input" "$sig"
}
