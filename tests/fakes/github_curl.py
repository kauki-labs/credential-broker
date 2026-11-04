#!/usr/bin/env python3
"""Fake GitHub behind a fake curl, for testing tests/acceptance_live.py itself. FAKE_GH picks one
misbehaviour; unset means an honest GitHub. State (revoked tokens, the branch) lives in FAKE_DIR."""
import json
import os
import re
import sys

d = os.environ["FAKE_DIR"]
mode = os.environ.get("FAKE_GH", "")
argv = sys.argv[1:]
method, writeout, data, url, header = "GET", False, "", "", ""
for i, a in enumerate(argv):
    if a == "-X":
        method = argv[i + 1]
    elif a == "-w":
        writeout = True
    elif a == "-d":
        data = argv[i + 1]
    elif a == "@-":
        header = sys.stdin.read()
    elif a.startswith("https://"):
        url = a
path = url.removeprefix("https://api.github.com")
m = re.search(r"^Authorization: Bearer (\S+)$", header, re.M)
token = m.group(1) if m else ""
os.makedirs(f"{d}/revoked", exist_ok=True)


def reply(status, body="{}"):
    if writeout:  # the suite's own calls: body, a newline and the status, always exit 0
        sys.stdout.write(f"{body}\n{status}")
        sys.exit(0)
    sys.stdout.write(body)
    sys.exit(22 if status >= 400 else 0)


SHA = "0123456789abcdef0123456789abcdef01234567"
DENIED = '{"message":"Resource not accessible by integration"}'
NOT_FOUND = '{"message":"Not Found"}'

# Minting: the token name records the profile the minter asked for.
if method == "POST" and re.fullmatch(r"/app/installations/[^/]+/access_tokens", path):
    body = json.loads(data)
    if body["permissions"]["contents"] == "write":
        t = "ghs_write"
    elif len(body["repositories"]) == 2:
        t = "ghs_readboth"
    else:
        t = "ghs_readone"
    reply(201, json.dumps({"token": t}))
if os.path.exists(f"{d}/revoked/{token}"):
    reply(401, '{"message":"Bad credentials"}')

perms = {"contents": "write", "issues": "write", "metadata": "read", "pull_requests": "write"}
if mode == "admin_perm":
    perms["administration"] = "write"
selection = "all" if mode == "sel_all" else "selected"
branch = f"{d}/branch"
route = f"{method} {path}"

if route == "GET /app/installations/4242":
    reply(200, json.dumps({"repository_selection": selection, "permissions": perms}))
elif route == "GET /installation/repositories":
    names = ["sandy", "credential-broker"] if token == "ghs_readboth" else ["credential-broker"]
    reply(200, json.dumps({"repositories": [{"name": n} for n in names]}))
elif route == "DELETE /installation/token":
    if mode == "revoke_fail":
        reply(403, '{"message":"nope"}')
    open(f"{d}/revoked/{token}", "w").close()
    reply(204)
elif route == "GET /repos/kauki-labs/sandy":
    if token != "ghs_readboth":
        reply(404, NOT_FOUND)
    reply(200, json.dumps({"private": mode != "public_other", "default_branch": "main"}))
elif route in ("GET /repos/kauki-labs/credential-broker/git/ref/heads/main",
               "GET /repos/kauki-labs/sandy/git/ref/heads/main"):
    reply(200, json.dumps({"object": {"sha": SHA}}))
elif route == "POST /repos/kauki-labs/credential-broker/git/refs":
    if token == "ghs_write" or mode == "read_writes":
        open(branch, "w").close()
        reply(201)
    reply(403, DENIED)
elif method == "DELETE" and path.startswith("/repos/kauki-labs/credential-broker/git/refs/heads/"):
    if token != "ghs_write":
        reply(403, DENIED)
    if os.path.exists(branch):
        os.unlink(branch)
    reply(204)
elif route == "POST /repos/kauki-labs/sandy/git/refs":
    reply(404, NOT_FOUND)
elif route in ("GET /repos/kauki-labs/credential-broker/actions/secrets", "PATCH /repos/kauki-labs/credential-broker"):
    reply(403, '{"message":"denied"}')
reply(599, json.dumps({"message": f"fake has no route for {method} {path}"}))
