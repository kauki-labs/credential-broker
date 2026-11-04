#!/usr/bin/env python3
"""Offline acceptance tests for scripts/github-app-token. Never calls GitHub: a fake `curl` records
argv and the header the script feeds it on stdin. TOOL_UNDER_TEST overrides the script (mutation
checks). Run: python3 -W error -I -m unittest discover -s tests"""
import json
import os
import re
import time
import unittest
from pathlib import Path

from support import ROOT, APP_ID, Workspace, decode_jwt, verifies, write_private

TOOL = Path(os.environ.get("TOOL_UNDER_TEST", ROOT / "scripts" / "github-app-token"))
TOKEN = "ghs_faketoken123"
OK_RESPONSE = json.dumps({"token": TOKEN, "expires_at": "2099-01-01T00:00:00Z"})
READ = {"contents": "read", "issues": "read", "metadata": "read", "pull_requests": "read"}
WRITE = {"contents": "write", "issues": "write", "metadata": "read", "pull_requests": "write"}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace("record_curl.py", {"GH_APP_ID": APP_ID, "GH_APP_INSTALLATION_ID": "4242",
                                              "FAKE_RESPONSE": OK_RESPONSE})

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()

    def run_tool(self, *args, **kw):
        return self.ws.run(TOOL, *args, **kw)

    def must_fail(self, *args, **kw):
        """Non-zero exit, nothing on stdout, curl never reached."""
        r = self.run_tool(*args, **kw)
        self.assertNotEqual(r.returncode, 0, "exited 0")
        self.assertEqual(r.stdout, "", "wrote to stdout on failure")
        self.assertFalse(self.ws.called(), "reached the network layer")
        return r

    def sent_body(self):
        return json.loads(self.ws.body_after("-d"))


class HappyPath(Base):
    def test_output_and_jwt(self):
        before = int(time.time())
        r = self.run_tool("sandy", "credential-broker")
        after = int(time.time())
        self.assertEqual(r.stdout, TOKEN + "\n", "stdout must be exactly the token")
        self.assertEqual(r.stderr, "")
        self.assertEqual(self.ws.tmpdir_files(), [], "script left a file in TMPDIR")
        jwt = self.ws.jwt_sent()
        self.assertTrue(jwt, "no bearer header reached curl's stdin")
        header, claims, signing_input, sig = decode_jwt(jwt)
        self.assertEqual(header["alg"], "RS256")
        self.assertEqual(header["typ"], "JWT")
        self.assertEqual(claims["iss"], APP_ID)
        self.assertEqual(len(claims), 3, "unexpected extra claims")
        self.assertLess(claims["iat"], before, "iat must be in the past (clock drift allowance)")
        self.assertEqual(claims["exp"] - claims["iat"], 600)
        self.assertLessEqual(claims["exp"], after + 540, "exp is more than 9 minutes ahead of now")
        self.assertGreaterEqual(claims["exp"], before + 540, "exp is too early")
        self.assertTrue(verifies(self.ws.pub, signing_input, sig, self.ws.dir), "signature does not verify")
        self.assertFalse(verifies(self.ws.other_pub, signing_input, sig, self.ws.dir),
                         "signature verified under an unrelated key")


class CurlRequest(Base):
    def test_flags_url_and_secrecy(self):
        self.run_tool("sandy", "credential-broker")
        argv = self.ws.argv()
        self.assertEqual(argv[0], "-q", "curl -q must be the first argument (skips ~/.curlrc)")
        pairs = list(zip(argv, argv[1:]))
        for pair in (("--noproxy", "*"), ("--proto", "=https"), ("-X", "POST")):
            self.assertIn(pair, pairs)
        self.assertIn("--tlsv1.2", argv)
        for bad in ("-k --insecure --proxy -x --cacert --capath --proxy-insecure -L --location "
                    "--trace --trace-ascii -v --verbose").split():
            self.assertNotIn(bad, argv)
        self.assertIn("https://api.github.com/app/installations/4242/access_tokens", argv)
        self.assertFalse(any(self.ws.jwt_sent() in a for a in argv), "JWT leaked to argv")
        self.assertFalse(any(TOKEN in a for a in argv), "token on argv")

    def test_exact_bodies(self):
        self.run_tool("sandy", "credential-broker")
        self.assertEqual(self.sent_body(), {"permissions": READ, "repositories": ["sandy", "credential-broker"]})
        self.run_tool("--write", "sandy")
        self.assertEqual(self.sent_body(), {"permissions": WRITE, "repositories": ["sandy"]})
        self.run_tool("--", "sandy")
        self.assertEqual(self.sent_body()["repositories"], ["sandy"], "-- handling broke the repo list")

    def test_hostile_environment_cannot_widen_or_leak(self):
        r = self.run_tool("sandy", env={"GH_APP_DRY_RUN": "1", "GH_APP_PERMISSIONS": '{"contents":"write"}',
                                        "PERMISSIONS": "contents:write"})
        self.assertEqual(r.stdout, TOKEN + "\n", "GH_APP_DRY_RUN must not change the output")
        self.assertTrue(self.ws.called(), "GH_APP_DRY_RUN skipped the real request path")
        self.assertEqual(set(self.sent_body()["permissions"].values()), {"read"})


class Arguments(Base):
    def test_bad_repositories(self):
        cases = {
            "repo outside the allowlist": ["other-repo"],
            "one bad repo among good ones": ["sandy", "other-repo"],
            "allowlist is exact, not prefix": ["sandy-extra"],
            "json breakout repo": ['a","permissions":{"contents":"write"},"x":"'],
            "owner/name": ["kauki-labs/sandy"],
            "space": ["my repo"],
            "newline": ["sandy\nevil"],
            "dot-dot": [".."],
            "empty name": [""],
            "option-like repo": ["-x"],
            "long option": ["--yolo", "sandy"],
            "write flag but no repo": ["--write"],
            "no repositories": [],
        }
        for label, args in cases.items():
            with self.subTest(label):
                self.must_fail(*args)

    def test_bad_ids(self):
        cases = {
            "installation id path traversal": ({"GH_APP_INSTALLATION_ID": "1/../../../orgs/x"}, ()),
            "installation id with query": ({"GH_APP_INSTALLATION_ID": "1?x=y"}, ()),
            "installation id non-numeric": ({"GH_APP_INSTALLATION_ID": "abc"}, ()),
            "installation id empty": ({"GH_APP_INSTALLATION_ID": ""}, ()),
            "app id with quote": ({"GH_APP_ID": 'x","admin":"1'}, ()),
            "app id empty": ({"GH_APP_ID": ""}, ()),
            "missing app id": ({}, ("GH_APP_ID",)),
            "missing key file var": ({}, ("GH_APP_KEY_FILE",)),
            "missing installation var": ({}, ("GH_APP_INSTALLATION_ID",)),
        }
        for label, (env, unset) in cases.items():
            with self.subTest(label):
                self.must_fail("sandy", env=env, unset=unset)


class KeyFile(Base):
    def key_copy(self, mode):
        return write_private(self.ws.dir / f"k{mode:o}.pem", self.ws.key.read_text(), mode)

    def test_loose_modes_are_refused(self):
        for mode in (0o644, 0o640, 0o660, 0o604, 0o666, 0o700, 0o770, 0o777, 0o000):
            with self.subTest(f"{mode:o}"):
                self.must_fail("sandy", env={"GH_APP_KEY_FILE": self.key_copy(mode)})

    def test_mode_400_and_symlink_to_600_work(self):
        r = self.run_tool("sandy", env={"GH_APP_KEY_FILE": self.key_copy(0o400)})
        self.assertEqual(r.returncode, 0, r.stderr)
        link = self.ws.dir / "link.pem"
        link.symlink_to(self.ws.key)
        r = self.run_tool("sandy", env={"GH_APP_KEY_FILE": link})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unusable_keys_are_refused(self):
        loose = self.key_copy(0o644)
        looselink = self.ws.dir / "looselink.pem"
        looselink.symlink_to(loose)
        empty = write_private(self.ws.dir / "empty.pem", "")
        junk = write_private(self.ws.dir / "junk.pem", "not a pem\n")
        pubonly = write_private(self.ws.dir / "pubonly.pem", self.ws.pub.read_text())
        cases = {"symlink to a 0644 key": looselink, "key file missing": self.ws.dir / "nope.pem",
                 "empty key file": empty, "junk key file": junk, "key path is a directory": self.ws.dir,
                 "public key as signing key": pubonly}
        for label, path in cases.items():
            with self.subTest(label):
                self.must_fail("sandy", env={"GH_APP_KEY_FILE": path})


class Upstream(Base):
    def test_malformed_answers_fail_closed(self):
        for label, resp in {"no_token": '{"message":"nope"}', "null_token": '{"token":null}',
                            "bad_token": '{"token":"ghs_a b"}', "garbage": "<html>oops</html>", "empty": ""}.items():
            with self.subTest(label):
                r = self.run_tool("sandy", env={"FAKE_RESPONSE": resp})
                self.assertNotEqual(r.returncode, 0, "exited 0")
                self.assertEqual(r.stdout, "")
                self.assertEqual(self.ws.tmpdir_files(), [])
                self.assertNotIn("ghs_a", r.stderr, "a malformed token was echoed to stderr")

    def test_http_error_reaches_the_operator_without_secrets(self):
        r = self.run_tool("sandy", env={"FAKE_RESPONSE": '{"message":"Not Found: repository x is not in the installation"}',
                                        "FAKE_EXIT": "22"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("not in the installation", r.stderr, "GitHub's error body must reach stderr")
        self.assertNotIn(self.ws.jwt_sent(), r.stderr, "JWT printed on failure")
        self.assertNotIn("BEGIN", r.stderr, "key material printed on failure")

    def test_xtrace_does_not_leak_the_jwt(self):
        # SHELLOPTS=xtrace plus an attacker-chosen trace fd must not print the JWT
        trace = self.ws.dir / "trace"
        fd = os.open(trace, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            r = self.run_tool("sandy", env={"SHELLOPTS": "xtrace", "PS4": "+X ", "BASH_XTRACEFD": fd}, pass_fds=(fd,))
        finally:
            os.close(fd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, TOKEN + "\n", "token wrong under SHELLOPTS=xtrace")
        for text in (r.stderr, trace.read_text()):
            self.assertIsNone(re.search(r"Bearer|[A-Za-z0-9_-]{40,}", text), "xtrace leaked the JWT")


if __name__ == "__main__":
    unittest.main()
