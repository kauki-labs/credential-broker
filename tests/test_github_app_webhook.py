#!/usr/bin/env python3
"""Offline acceptance tests for scripts/github-app-webhook. Never calls GitHub: a fake `curl` records
argv, the stdin header and the body file. TOOL_UNDER_TEST overrides the script (mutation checks).
Run: python3 -W error -I -m unittest discover -s tests"""
import json
import os
import unittest
from pathlib import Path

from support import ROOT, APP_ID, Workspace, decode_jwt, verifies, write_private

TOOL = Path(os.environ.get("TOOL_UNDER_TEST", ROOT / "scripts" / "github-app-webhook"))
SECRET = "s3cr3t-webhook-value-0123456789"
URL = "https://hooks.example.org/github"


def answer(url):
    return json.dumps({"url": url, "content_type": "json", "secret": "********", "insecure_ssl": "0"})


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace("record_curl.py", {"GH_APP_ID": APP_ID, "GH_APP_WEBHOOK_SECRET_FILE": "{ws}/secret"})
        cls.secret_file = write_private(cls.ws.dir / "secret", SECRET + "\n")

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()

    def run_tool(self, *args, url=None, **kw):
        env = dict(kw.pop("env", None) or {})
        env.setdefault("FAKE_RESPONSE", answer(url or "http://localhost:8080/"))
        return self.ws.run(TOOL, *args, env=env, **kw)

    def must_fail(self, *args, **kw):
        """Non-zero exit, nothing on stdout, curl never reached, the secret never printed."""
        r = self.run_tool(*args, **kw)
        self.assertNotEqual(r.returncode, 0, "exited 0")
        self.assertEqual(r.stdout, "")
        self.assertFalse(self.ws.called(), "reached the network layer")
        self.assertNotIn(SECRET, r.stderr, "the secret reached stderr")
        return r


class SetCommand(Base):
    def test_output_jwt_and_request(self):
        r = self.run_tool("set", URL, url=URL)
        self.assertEqual(r.stdout, URL + "\n", "stdout must be the URL GitHub holds")
        self.assertEqual(r.stderr, "", "stderr not empty for a public URL")
        self.assertEqual(self.ws.tmpdir_files(), [])
        jwt = self.ws.jwt_sent()
        header, claims, signing_input, sig = decode_jwt(jwt)
        self.assertEqual(header["alg"], "RS256")
        self.assertEqual(claims["iss"], APP_ID)
        self.assertEqual(claims["exp"] - claims["iat"], 600)
        self.assertTrue(verifies(self.ws.pub, signing_input, sig, self.ws.dir), "bad JWT signature")

        argv = self.ws.argv()
        pairs = list(zip(argv, argv[1:]))
        self.assertEqual(argv[0], "-q", "curl -q must be the first argument")
        for pair in (("--noproxy", "*"), ("--proto", "=https"), ("-X", "PATCH")):
            self.assertIn(pair, pairs)
        self.assertIn("--tlsv1.2", argv)
        self.assertIn("https://api.github.com/app/hook/config", argv)
        for bad in "-k --insecure --proxy -L --location -v --verbose --trace".split():
            self.assertNotIn(bad, argv)
        self.assertFalse(any(jwt in a for a in argv), "JWT leaked to argv")
        self.assertFalse(any(SECRET in a for a in argv), "secret leaked to argv")
        self.assertEqual(json.loads((self.ws.fake / "body").read_text()),
                         {"content_type": "json", "insecure_ssl": "0", "secret": SECRET, "url": URL})
        self.assertEqual((self.ws.fake / "bodymode").read_text(), "600", "body file was not mode 600")
        self.assertNotIn(SECRET, r.stderr + self.ws.stdin(), "secret reached stderr or the stdin header")

    def test_localhost_url_warns_and_prints_only_the_url(self):
        r = self.run_tool("set", "http://localhost:8080/")
        self.assertEqual(r.stdout, "http://localhost:8080/\n")
        self.assertIn("cannot reach a localhost URL", r.stderr)


class ShowCommand(Base):
    def test_prints_non_secret_fields_with_a_get(self):
        r = self.run_tool("show")
        self.assertEqual(r.stdout, '{"url":"http://localhost:8080/","content_type":"json","insecure_ssl":"0"}\n')
        self.assertNotRegex(r.stdout, r"secret|\*\*\*")
        self.assertIn("GET", self.ws.argv())
        self.assertFalse((self.ws.fake / "body").exists(), "show must not send a body")

    def test_does_not_need_the_secret_file(self):
        r = self.run_tool("show", env={"GH_APP_WEBHOOK_SECRET_FILE": "/nonexistent"})
        self.assertEqual(r.returncode, 0, r.stderr)


class Validation(Base):
    def test_bad_arguments_and_urls(self):
        cases = {
            "no subcommand": [], "unknown subcommand": ["delete"], "set without a url": ["set"],
            "set with extra arguments": ["set", "https://a.example/x", "https://b.example/y"],
            "show with an argument": ["show", "extra"],
            "url without a scheme": ["set", "hooks.example.org/x"], "ftp url": ["set", "ftp://hooks.example.org/x"],
            "url with a space": ["set", "https://a.example/x y"],
            "url with a quote (json breakout)": ["set", 'https://a.example/","x":"'],
            "url with a backslash": ["set", "https://a.example/\\x"],
            "url with a newline": ["set", "https://a.example/x\ny"],
            "url starting with a dash": ["set", "--", "-d"], "empty url": ["set", ""],
        }
        for label, args in cases.items():
            with self.subTest(label):
                self.must_fail(*args)

    def test_bad_secret_files(self):
        d = self.ws.dir
        cases = {
            "under 16 characters": write_private(d / "short", "tooshort\n"),
            "two lines": write_private(d / "two", SECRET + "\nsecondline-of-secret-0123\n"),
            "spaces": write_private(d / "space", "has a space in it 0123456789\n"),
            "empty": write_private(d / "empty", ""),
            "mode 644": write_private(d / "loose", SECRET + "\n", 0o644),
            "missing": d / "none",
        }
        for label, path in cases.items():
            with self.subTest(label):
                self.must_fail("set", URL, env={"GH_APP_WEBHOOK_SECRET_FILE": path})
        with self.subTest("secret env unset"):
            self.must_fail("set", URL, unset=("GH_APP_WEBHOOK_SECRET_FILE",))

    def test_bad_keys_and_ids(self):
        d = self.ws.dir
        cases = {
            "key mode 644": ({"GH_APP_KEY_FILE": write_private(d / "loosekey.pem", self.ws.key.read_text(), 0o644)}, ()),
            "missing key file": ({"GH_APP_KEY_FILE": d / "none.pem"}, ()),
            "key file that is not a key": ({"GH_APP_KEY_FILE": write_private(d / "bad.pem", "not a key\n")}, ()),
            "GH_APP_ID unset": ({}, ("GH_APP_ID",)),
            "GH_APP_ID with a quote": ({"GH_APP_ID": 'a"b'}, ()),
        }
        for label, (env, unset) in cases.items():
            with self.subTest(label):
                self.must_fail("show", env=env, unset=unset)

    def test_symlink_to_a_600_key_works(self):
        link = self.ws.dir / "link.pem"
        link.symlink_to(self.ws.key)
        r = self.run_tool("show", env={"GH_APP_KEY_FILE": link})
        self.assertEqual(r.returncode, 0, r.stderr)


class Upstream(Base):
    def test_failures_fail_closed_without_secrets(self):
        cases = {"http_err": ('{"message":"Bad credentials"}', "22"), "no_url": ('{"message":"nope"}', "0"),
                 "garbage": ("<html>oops</html>", "0")}
        for label, (resp, code) in cases.items():
            with self.subTest(label):
                r = self.run_tool("set", URL, env={"FAKE_RESPONSE": resp, "FAKE_EXIT": code})
                self.assertNotEqual(r.returncode, 0, "exited 0")
                self.assertEqual(r.stdout, "")
                self.assertNotIn(SECRET, r.stderr)
                self.assertEqual(self.ws.tmpdir_files(), [], "left a file in TMPDIR")

    def test_show_rejects_a_garbage_answer(self):
        r = self.run_tool("show", env={"FAKE_RESPONSE": "<html>oops</html>"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


class HostileEnvironment(Base):
    def test_xtrace_does_not_leak_the_secret_or_the_jwt(self):
        r = self.run_tool("set", URL, url=URL, env={"SHELLOPTS": "xtrace", "BASH_XTRACEFD": "2"})
        self.assertEqual(r.returncode, 0, r.stderr[:300])
        self.assertTrue(self.ws.called(), "the hostile-environment run never reached curl")
        self.assertEqual(r.stdout, URL + "\n")
        self.assertNotIn(SECRET, r.stderr, "xtrace leaked the secret")
        self.assertNotIn(self.ws.jwt_sent(), r.stderr, "xtrace leaked the JWT")
        self.assertEqual(self.ws.tmpdir_files(), [], "TMPDIR not clean after the hostile run")


if __name__ == "__main__":
    unittest.main()
