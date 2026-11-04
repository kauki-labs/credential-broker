#!/usr/bin/env python3
"""Live acceptance tests for the `external` GitHub App, run by the operator after registering it.
Talks to the real api.github.com with real tokens from scripts/github-app-token and with the App's
own JWT. It touches one throwaway branch on credential-broker and deletes it again.

  ACCEPT_LIVE=1 GH_APP_ID=... GH_APP_KEY_FILE=... GH_APP_INSTALLATION_ID=... \\
    python3 -I tests/acceptance_live.py

Not part of CI: the key must never be there. tests/test_acceptance_live.py runs this file against
a fake GitHub. Tokens never reach argv or output; failure messages show at most 300 bytes of a body.
The tests run in name order and share three tokens: A8 and A9 revoke them."""
import base64
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
MINTER = ROOT / "scripts" / "github-app-token"
API = "https://api.github.com"
OWNER, REPO, OTHER = "kauki-labs", "credential-broker", "sandy"
CEILING = {"contents": "write", "issues": "write", "metadata": "read", "pull_requests": "write"}
NEEDED = ("GH_APP_ID", "GH_APP_KEY_FILE", "GH_APP_INSTALLATION_ID")


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def app_jwt():
    """The App's own JWT, built here and not by the minter, for the checks that read the installation."""
    now = int(time.time())
    claims = {"iat": now - 60, "exp": now + 540, "iss": os.environ["GH_APP_ID"]}
    signing_input = b64url(b'{"alg":"RS256","typ":"JWT"}') + b"." + b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", os.environ["GH_APP_KEY_FILE"]],
                         input=signing_input, capture_output=True, check=True).stdout
    return (signing_input + b"." + b64url(sig)).decode()


def api(token, method, path, data=None):
    """Return (HTTP status, body text). The token goes to curl on stdin, never on argv."""
    cmd = ["curl", "-q", "-sS", "--proto", "=https", "--tlsv1.2", "--noproxy", "*", "-w", "\n%{http_code}",
           "-X", method, "-H", "@-", "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"]
    if data is not None:
        cmd += ["-d", json.dumps(data)]
    r = subprocess.run(cmd + [API + path], input=f"Authorization: Bearer {token}\n", capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"curl failed ({r.returncode}): {r.stderr[:300]}")
    body, _, status = r.stdout.rpartition("\n")
    return int(status), body


def mint(*args):
    r = subprocess.run([str(MINTER), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"minter {' '.join(args)} failed ({r.returncode}): {r.stderr[:300]}")
    return r.stdout.strip()


def enabled():
    return os.environ.get("ACCEPT_LIVE") == "1" and all(os.environ.get(k) for k in NEEDED)


@unittest.skipUnless(enabled(), "set ACCEPT_LIVE=1, GH_APP_ID, GH_APP_KEY_FILE and GH_APP_INSTALLATION_ID")
class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.branch = f"acceptance/{int(time.time())}-{os.getpid()}"
        cls.live = {}
        # minted early so tearDownClass can always remove the branch and revoke the tokens
        for name, args in (("read_both", (REPO, OTHER)), ("read_one", (REPO,)), ("write", ("--write", REPO))):
            cls.live[name] = mint(*args)

    @classmethod
    def tearDownClass(cls):
        # best effort, on success and on failure: delete the throwaway branch, then revoke every token left
        if "write" in cls.live:
            try:
                api(cls.live["write"], "DELETE", f"/repos/{OWNER}/{REPO}/git/refs/heads/{cls.branch}")
            except AssertionError:
                pass
        for token in cls.live.values():
            try:
                api(token, "DELETE", "/installation/token")
            except AssertionError:
                pass

    # helpers
    def call(self, label, want, token, method, path, data=None):
        status, body = api(token, method, path, data)
        self.assertEqual(status, want, f"{label}: expected HTTP {want}, got {status}: {body[:300]}")
        return json.loads(body) if body else {}

    def denied(self, label, token, method, path, data=None):
        status, body = api(token, method, path, data)  # a refused write is 403 or 404, by what GitHub reveals
        self.assertIn(status, (403, 404), f"{label}: expected 403 or 404, got {status}: {body[:300]}")

    def names(self, body):
        return sorted(r["name"] for r in body["repositories"])

    # A1. The installation is limited to selected repos, and a token reaches exactly those
    def test_a1_installation_is_limited_to_selected_repositories(self):
        inst = self.call("A1 read the installation as the App", 200, app_jwt(), "GET",
                         f"/app/installations/{os.environ['GH_APP_INSTALLATION_ID']}")
        self.assertEqual(inst["repository_selection"], "selected", "installation is not 'Only select repositories'")
        repos = self.call("A1 token lists installation repos", 200, self.live["read_both"], "GET", "/installation/repositories")
        self.assertEqual(self.names(repos), [REPO, OTHER], "installation reaches other repos")

    # A2. The App ceiling is exactly the documented permission set
    def test_a2_app_ceiling_is_the_documented_permission_set(self):
        inst = self.call("A2 read the App permissions", 200, app_jwt(), "GET",
                         f"/app/installations/{os.environ['GH_APP_INSTALLATION_ID']}")
        self.assertEqual(inst["permissions"], CEILING)

    # A3. A token narrowed to one repo reaches only that repo
    def test_a3_narrowed_token_reaches_only_its_repo(self):
        repos = self.call("A3 narrowed token lists repos", 200, self.live["read_one"], "GET", "/installation/repositories")
        self.assertEqual(self.names(repos), [REPO], "narrowed token reaches more than one repo")
        other = self.call(f"A3 look up {OTHER}", 200, self.live["read_both"], "GET", f"/repos/{OWNER}/{OTHER}")
        if not other["private"]:
            self.skipTest(f"{OTHER} is public, so a narrowed token can read it; the A5 write check covers separation")
        self.call(f"A3 narrowed token cannot see private {OTHER}", 404, self.live["read_one"], "GET", f"/repos/{OWNER}/{OTHER}")

    # A4. A read token reads but cannot write
    def test_a4_read_token_reads_but_cannot_write(self):
        ref = self.call("A4 read token reads the default branch ref", 200, self.live["read_one"], "GET",
                        f"/repos/{OWNER}/{REPO}/git/ref/heads/main")
        sha = ref["object"]["sha"]
        self.assertRegex(sha, r"^[0-9a-f]{40}$", "no commit sha in the ref response")
        self.denied("A4 read token cannot create a branch", self.live["read_one"], "POST",
                    f"/repos/{OWNER}/{REPO}/git/refs", {"ref": f"refs/heads/{self.branch}", "sha": sha})

    # A5. A write token writes to its repo and only its repo
    def test_a5_write_token_writes_to_its_repo_only(self):
        ref = self.call("A5 read the default branch ref", 200, self.live["write"], "GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/main")
        self.call("A5 write token creates a branch", 201, self.live["write"], "POST", f"/repos/{OWNER}/{REPO}/git/refs",
                  {"ref": f"refs/heads/{self.branch}", "sha": ref["object"]["sha"]})
        self.call("A5 write token deletes the branch", 204, self.live["write"], "DELETE",
                  f"/repos/{OWNER}/{REPO}/git/refs/heads/{self.branch}")
        other = self.call(f"A5 look up {OTHER}", 200, self.live["read_both"], "GET", f"/repos/{OWNER}/{OTHER}")
        other_ref = self.call(f"A5 read the {OTHER} default branch", 200, self.live["read_both"], "GET",
                              f"/repos/{OWNER}/{OTHER}/git/ref/heads/{other['default_branch']}")
        self.denied(f"A5 write token for {REPO} cannot write to {OTHER}", self.live["write"], "POST",
                    f"/repos/{OWNER}/{OTHER}/git/refs", {"ref": f"refs/heads/{self.branch}", "sha": other_ref["object"]["sha"]})

    # A6. The token carries no permission outside the ceiling
    def test_a6_no_permission_outside_the_ceiling(self):
        self.denied("A6 write token cannot read repository secrets", self.live["write"], "GET", f"/repos/{OWNER}/{REPO}/actions/secrets")
        self.denied("A6 write token cannot change repository settings", self.live["write"], "PATCH",
                    f"/repos/{OWNER}/{REPO}", {"has_wiki": True})

    # A7. The minter refuses a repo outside its allowlist, with the allowlist error
    def test_a7_minter_refuses_a_repo_outside_the_allowlist(self):
        r = subprocess.run([str(MINTER), "some-other-repo"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, f"minter exit code {r.returncode}, want 2")
        self.assertIn("repository not in the allowlist", r.stderr, f"minter refused for another reason: {r.stderr[:300]}")

    # A8. Early revoke kills the token and only that token
    def test_a8_early_revoke_kills_only_that_token(self):
        self.call("A8 token works before revoke", 200, self.live["read_one"], "GET", "/installation/repositories")
        self.call("A8 DELETE /installation/token", 204, self.live["read_one"], "DELETE", "/installation/token")
        for _ in range(5):  # GitHub may take a moment to refuse the token
            status, _ = api(self.live["read_one"], "GET", "/installation/repositories")
            if status == 401:
                break
            time.sleep(1)
        self.assertEqual(status, 401, "revoked token is still accepted")
        del self.live["read_one"]
        self.call("A8 revoking one token leaves others alone", 200, self.live["read_both"], "GET", "/installation/repositories")

    # A9. Every token is revoked at the end, and the revoke is checked
    def test_a9_every_token_is_revoked(self):
        self.call("A9 revoke the write token", 204, self.live["write"], "DELETE", "/installation/token")
        del self.live["write"]
        self.call("A9 revoke the shared read token", 204, self.live["read_both"], "DELETE", "/installation/token")
        del self.live["read_both"]


if __name__ == "__main__":
    if not enabled():  # a skipped run must not look like a pass
        print("set ACCEPT_LIVE=1, GH_APP_ID, GH_APP_KEY_FILE and GH_APP_INSTALLATION_ID to run against the real GitHub API",
              file=sys.stderr)
        sys.exit(2)
    unittest.main(verbosity=2)
