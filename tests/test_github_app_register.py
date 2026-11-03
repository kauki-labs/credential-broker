#!/usr/bin/env python3
"""Offline acceptance tests for scripts/github-app-register. GitHub is a fake: the exchange is
injected, and the real loopback server is driven over real sockets. Run: python3 -I tests/test_github_app_register.py"""
import html
import http.client
import importlib.machinery
import importlib.util
import json
import os
import re
import stat
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "github-app-register"
loader = importlib.machinery.SourceFileLoader("reg", str(SCRIPT))
spec = importlib.util.spec_from_loader("reg", loader)
reg = importlib.util.module_from_spec(spec)
loader.exec_module(reg)

TEMPLATE = json.loads((ROOT / "apps" / "external.kauki-labs.manifest.json").read_text())
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIFAKE\n-----END RSA PRIVATE KEY-----\n"
GOOD = {"id": 4242, "client_id": "Iv1.abc", "slug": "external-actor", "html_url": "https://github.com/apps/external-actor",
        "pem": PEM, "webhook_secret": "whsec-0123456789abcdef", "client_secret": "CLIENT-SECRET-MUST-NOT-LAND"}
CODE = "1e88e6b7c8db3db09993cd1a5c1be68a56913647"


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class Manifest(unittest.TestCase):
    def build(self, broker="http://localhost:8080", name="external actor", public=False):
        return reg.build_manifest(TEMPLATE, broker, "http://127.0.0.1:9/callback", name, public)

    def test_urls_derive_from_the_broker_url(self):
        m = self.build("https://broker.example.org/")
        self.assertEqual(m["hook_attributes"], {"url": "https://broker.example.org/github/webhook", "active": True})
        self.assertEqual(m["setup_url"], "https://broker.example.org/github/setup")

    def test_default_points_at_localhost(self):
        self.assertEqual(self.build()["hook_attributes"]["url"], "http://localhost:8080/github/webhook")

    def test_template_fields_carry_over_and_the_template_is_untouched(self):
        before = json.dumps(TEMPLATE, sort_keys=True)
        m = self.build()
        for key in ("url", "description", "default_events", "default_permissions", "setup_on_update"):
            self.assertEqual(m[key], TEMPLATE[key])
        self.assertEqual(json.dumps(TEMPLATE, sort_keys=True), before)

    def test_redirect_and_visibility(self):
        self.assertEqual(self.build()["redirect_url"], "http://127.0.0.1:9/callback")
        self.assertIs(self.build(public=False)["public"], False)
        self.assertIs(self.build(public=True)["public"], True)

    def test_permissions_are_never_widened(self):
        self.assertEqual(self.build()["default_permissions"],
                         {"metadata": "read", "contents": "write", "pull_requests": "write", "issues": "write"})

    def test_hostile_broker_urls_are_refused(self):
        for bad in ("localhost:8080", "ftp://x.example/", "https://a b", 'https://a"x', "https://a\\b", "https://a\nb", "", "javascript:alert(1)"):
            with self.assertRaises(reg.RegisterError, msg=bad):
                self.build(broker=bad)

    def test_hostile_names_are_refused(self):
        for bad in ('a"b', "<b>", "x" * 40, "", " lead", "a\nb"):
            with self.assertRaises(reg.RegisterError, msg=bad):
                self.build(name=bad)

    def test_organization_names(self):
        self.assertEqual(reg.owner_url(None), "https://github.com/settings/apps/new")
        self.assertEqual(reg.owner_url("kauki-labs"), "https://github.com/organizations/kauki-labs/settings/apps/new")
        for bad in ("a/b", "a b", "../x", "", "-x", "x" * 40):
            with self.assertRaises(reg.RegisterError, msg=bad):
                reg.owner_url(bad)


class Page(unittest.TestCase):
    def test_form_posts_the_manifest_with_state_and_round_trips(self):
        manifest = reg.build_manifest(TEMPLATE, "http://localhost:8080", "http://127.0.0.1:9/callback", "external actor", False)
        body = reg.page(manifest, "https://github.com/settings/apps/new", "STATE123")
        self.assertIn('action="https://github.com/settings/apps/new?state=STATE123"', body)
        self.assertIn('method="post"', body)
        value = re.search(r'name="manifest" value="([^"]*)"', body).group(1)
        self.assertEqual(json.loads(html.unescape(value)), manifest)


class Exchange(unittest.TestCase):
    def opener(self, payload=None, error=None):
        calls = []

        def urlopen(request, timeout):
            calls.append((request, timeout))
            if error:
                raise error
            return _Resp(payload)

        return urlopen, calls

    def test_posts_to_the_conversion_endpoint(self):
        urlopen, calls = self.opener(json.dumps(GOOD))
        self.assertEqual(reg.exchange(CODE, urlopen)["slug"], "external-actor")
        request, timeout = calls[0]
        self.assertEqual(request.full_url, f"https://api.github.com/app-manifests/{CODE}/conversions")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")
        self.assertLessEqual(timeout, 30)

    def test_a_bad_code_never_reaches_the_network(self):
        urlopen, calls = self.opener(json.dumps(GOOD))
        for bad in ("", "short", "../../x", CODE + "/extra", CODE + "\n", "a" * 200):
            with self.assertRaises(reg.RegisterError, msg=bad):
                reg.exchange(bad, urlopen)
        self.assertEqual(calls, [])

    def test_a_404_is_reported_not_raised_raw(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, _Resp('{"message":"Not Found"}'))
        urlopen, _ = self.opener(error=err)
        with self.assertRaisesRegex(reg.RegisterError, "HTTP 404"):
            reg.exchange(CODE, urlopen)

    def test_garbage_and_network_errors(self):
        for kwargs in ({"payload": "<html>"}, {"error": urllib.error.URLError("down")}, {"error": TimeoutError()}):
            urlopen, _ = self.opener(**kwargs)
            with self.assertRaises(reg.RegisterError):
                reg.exchange(CODE, urlopen)


class _Resp:
    def __init__(self, text):
        self.text = text

    def read(self, *a):
        return self.text.encode() if isinstance(self.text, str) else self.text

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Save(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "cfg"

    def tearDown(self):
        self.tmp.cleanup()

    def test_files_are_private_and_hold_the_key_and_secret(self):
        summary = reg.save(GOOD, self.out)
        self.assertEqual(mode(self.out), 0o700)
        for path in (summary["key_file"], summary["webhook_secret_file"]):
            self.assertEqual(mode(path), 0o600)
        self.assertEqual(Path(summary["key_file"]).read_text(), PEM)
        self.assertEqual(Path(summary["webhook_secret_file"]).read_text(), GOOD["webhook_secret"] + "\n")

    def test_the_client_secret_is_discarded_and_the_summary_holds_no_secret(self):
        summary = reg.save(GOOD, self.out)
        for path in self.out.iterdir():
            self.assertNotIn("CLIENT-SECRET", path.read_text())
        flat = json.dumps(summary)
        for secret in ("CLIENT-SECRET", "MIIFAKE", GOOD["webhook_secret"]):
            self.assertNotIn(secret, flat)

    def test_an_existing_key_is_never_overwritten_and_the_new_key_is_not_lost(self):
        self.out.mkdir(mode=0o700)
        old = self.out / "external-actor.pem"
        old.write_text("OLD KEY")
        summary = reg.save(GOOD, self.out)
        self.assertEqual(old.read_text(), "OLD KEY")
        self.assertEqual(summary["key_file"], str(self.out / "external-actor.pem.1"))
        self.assertEqual(Path(summary["key_file"]).read_text(), PEM)
        self.assertEqual(mode(summary["key_file"]), 0o600)

    def test_a_symlink_at_the_target_is_not_followed(self):
        self.out.mkdir(mode=0o700)
        victim = Path(self.tmp.name) / "victim"
        victim.write_text("KEEP")
        (self.out / "external-actor.pem").symlink_to(victim)
        reg.save(GOOD, self.out)
        self.assertEqual(victim.read_text(), "KEEP")

    def test_hostile_or_malformed_answers_write_nothing(self):
        cases = [
            {**GOOD, "slug": "../evil"}, {**GOOD, "slug": "a/b"}, {**GOOD, "slug": ""}, {**GOOD, "slug": "Upper"},
            {**GOOD, "pem": "not a key"}, {**GOOD, "pem": None}, {**GOOD, "webhook_secret": ""},
            {k: v for k, v in GOOD.items() if k != "pem"}, {k: v for k, v in GOOD.items() if k != "id"}, None, [],
        ]
        for bad in cases:
            with self.assertRaises(reg.RegisterError, msg=str(bad)[:60]):
                reg.save(bad, self.out)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))


class Server(unittest.TestCase):
    def start(self, do_exchange):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server, self.ctx = reg.make_server(TEMPLATE, "http://localhost:8080", None, "external actor", False,
                                                Path(self.tmp.name) / "cfg", 0, do_exchange)
        self.port = self.ctx["port"]
        self.thread = threading.Thread(target=reg.serve, args=(self.server, self.ctx, 15), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.ctx["done"].append(("stop", None)) if not self.ctx["done"] else None
        self.thread.join(5)
        self.server.server_close()

    def get(self, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{self.port}"})
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
        return resp.status, body

    def test_serves_the_form_bound_to_loopback_only(self):
        self.start(lambda code: GOOD)
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(f"state={self.ctx['state']}", body)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertIn(f"http://127.0.0.1:{self.port}/callback", html.unescape(body))

    def test_a_wrong_state_or_host_is_refused_and_does_not_burn_the_code(self):
        calls = []
        self.start(lambda code: calls.append(code) or GOOD)
        self.assertEqual(self.get(f"/callback?code={CODE}&state=wrong")[0], 400)
        self.assertEqual(self.get(f"/callback?code={CODE}")[0], 400)
        self.assertEqual(self.get(f"/callback?code={CODE}&state=%C3%A9")[0], 400)
        self.assertEqual(self.get(f"/callback?code={CODE}&state={self.ctx['state']}", host="evil.example")[0], 400)
        self.assertEqual(self.get("/", host="evil.example:80")[0], 400)
        self.assertEqual(self.get("/other")[0], 404)
        self.assertEqual(calls, [])
        self.assertEqual(self.ctx["done"], [])

    def test_a_good_callback_registers_once(self):
        calls = []
        self.start(lambda code: calls.append(code) or GOOD)
        status, body = self.get(f"/callback?code={CODE}&state={self.ctx['state']}")
        self.assertEqual(status, 200)
        self.thread.join(5)
        self.assertEqual(calls, [CODE])
        kind, summary = self.ctx["done"][0]
        self.assertEqual(kind, "ok")
        self.assertEqual(mode(summary["key_file"]), 0o600)
        for secret in ("MIIFAKE", GOOD["webhook_secret"], "CLIENT-SECRET"):
            self.assertNotIn(secret, body)

    def test_a_failed_exchange_is_reported_and_ends_the_run(self):
        def boom(code):
            raise reg.RegisterError("GitHub answered HTTP 404: <script>x</script>")

        self.start(boom)
        status, body = self.get(f"/callback?code={CODE}&state={self.ctx['state']}")
        self.assertEqual(status, 502)
        self.assertNotIn("<script>", body)
        self.thread.join(5)
        self.assertEqual(self.ctx["done"][0][0], "error")

    def test_a_malformed_code_with_a_good_state_ends_with_an_error_and_no_network(self):
        calls = []
        self.start(lambda code: calls.append(code) or GOOD)
        self.assertEqual(self.get(f"/callback?code=../x&state={self.ctx['state']}")[0], 502)
        self.thread.join(5)
        self.assertEqual(self.ctx["done"][0][0], "error")
        self.assertEqual(calls, [])


class Main(unittest.TestCase):
    def test_bad_broker_url_exits_2_before_serving(self):
        self.assertEqual(reg.main(["--broker-url", "not a url", "--out-dir", tempfile.gettempdir()]), 2)

    def test_timeout_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(reg.main(["--timeout", "0.2", "--out-dir", tmp]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
