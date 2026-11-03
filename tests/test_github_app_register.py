#!/usr/bin/env python3
"""Offline acceptance tests for scripts/github-app-register. GitHub is a fake: the exchange is
injected, and the real loopback server is driven over real sockets. Run: python3 -I tests/test_github_app_register.py"""
import contextlib
import html
import http.client
import io
import importlib.machinery
import importlib.util
import json
import os
import re
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path

sys.dont_write_bytecode = True  # loading the script must not drop a __pycache__ into scripts/
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

    def test_the_key_is_kept_whatever_else_is_wrong(self):
        # GitHub shows the key once and the code is spent: never lose it to a later check.
        cases = {
            "empty secret": {**GOOD, "webhook_secret": ""},
            "no secret": {k: v for k, v in GOOD.items() if k != "webhook_secret"},
            "no id": {k: v for k, v in GOOD.items() if k != "id"},
            "no html_url": {k: v for k, v in GOOD.items() if k != "html_url"},
            "no slug": {k: v for k, v in GOOD.items() if k != "slug"},
            "slug with an underscore": {**GOOD, "slug": "external_actor"},
            "slug in capitals": {**GOOD, "slug": "External-Actor"},
        }
        for label, response in cases.items():
            out = self.out / label.replace(" ", "-")
            summary = reg.save(response, out)
            self.assertEqual(Path(summary["key_file"]).read_text(), PEM, label)
            self.assertEqual(mode(summary["key_file"]), 0o600, label)

    def test_a_missing_webhook_secret_is_reported_as_a_warning(self):
        summary = reg.save({**GOOD, "webhook_secret": ""}, self.out)
        self.assertIsNone(summary["webhook_secret_file"])
        self.assertIn("webhook secret", summary["warning"])

    def test_a_hostile_slug_cannot_leave_the_output_directory(self):
        for slug in ("../evil", "a/b", "..", "/etc/passwd", "x\ny", "\u00e9"):
            out = self.out / "d"
            summary = reg.save({**GOOD, "slug": slug}, out)
            self.assertEqual(Path(summary["key_file"]).parent, out, slug)
        self.assertEqual({p.name for p in self.out.iterdir()}, {"d"})

    def test_an_answer_without_a_key_writes_nothing(self):
        for bad in ({**GOOD, "pem": "not a key"}, {**GOOD, "pem": None}, {k: v for k, v in GOOD.items() if k != "pem"}, None, []):
            with self.assertRaises(reg.RegisterError, msg=str(bad)[:60]):
                reg.save(bad, self.out)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))


class Server(unittest.TestCase):
    def start(self, do_exchange, conn_timeout=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server, self.ctx = reg.make_server(TEMPLATE, "http://localhost:8080", None, "external actor", False,
                                                Path(self.tmp.name) / "cfg", 0, do_exchange)
        if conn_timeout is not None:
            self.ctx["conn_timeout"] = conn_timeout
        self.port = self.ctx["port"]
        self.base = f"/{self.ctx['token']}"
        self.thread = threading.Thread(target=reg.serve, args=(self.server, self.ctx, 15), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        if not self.ctx["done"]:
            self.ctx["done"].append(("stop", None))
        self.thread.join(5)
        self.server.server_close()

    def get(self, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{self.port}"})
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
        return resp.status, body

    def callback(self, **overrides):
        params = {"code": CODE, "state": self.ctx["state"], **overrides}
        return self.get(f"{self.base}/callback?" + "&".join(f"{k}={v}" for k, v in params.items() if v is not None))

    def test_serves_the_form_on_the_secret_path_bound_to_loopback_only(self):
        self.start(lambda code: GOOD)
        status, body = self.get(f"{self.base}/")
        self.assertEqual(status, 200)
        self.assertIn(f"state={self.ctx['state']}", body)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertIn(f"http://127.0.0.1:{self.port}{self.base}/callback", html.unescape(body))

    def test_without_the_secret_token_nothing_is_served(self):
        calls = []
        self.start(lambda code: calls.append(code) or GOOD)
        for path in ("/", "/callback", f"/callback?code={CODE}&state={self.ctx['state']}", "/wrong/", "/wrong/callback",
                     f"/wrong/callback?code={CODE}&state={self.ctx['state']}", f"{self.base}", "//"):
            self.assertEqual(self.get(path)[0], 404, path)
        self.assertNotIn(self.ctx["state"], self.get("/")[1])
        self.assertEqual(calls, [])
        self.assertEqual(self.ctx["done"], [])

    def test_a_wrong_state_or_host_is_refused_and_does_not_burn_the_code(self):
        calls = []
        self.start(lambda code: calls.append(code) or GOOD)
        self.assertEqual(self.callback(state="wrong")[0], 400)
        self.assertEqual(self.callback(state=None)[0], 400)
        self.assertEqual(self.callback(state="%C3%A9")[0], 400)
        self.assertEqual(self.get(f"{self.base}/callback?code={CODE}&state={self.ctx['state']}", host="evil.example")[0], 400)
        self.assertEqual(self.get(f"{self.base}/", host="evil.example:80")[0], 400)
        self.assertEqual(calls, [])
        self.assertEqual(self.ctx["done"], [])

    def test_a_good_callback_registers_once(self):
        calls = []
        self.start(lambda code: calls.append(code) or GOOD)
        status, body = self.callback()
        self.assertEqual(status, 200)
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive(), "the server must stop after the first valid callback")
        self.assertEqual(calls, [CODE])
        kind, summary = self.ctx["done"][0]
        self.assertEqual(kind, "ok")
        self.assertEqual(mode(summary["key_file"]), 0o600)
        for secret in ("MIIFAKE", GOOD["webhook_secret"], "CLIENT-SECRET"):
            self.assertNotIn(secret, body)

    def test_every_failure_ends_the_run_with_an_error_instead_of_hanging(self):
        failures = {
            "registration error": reg.RegisterError("GitHub answered HTTP 404: <script>x</script>"),
            "connection reset": ConnectionResetError("secret-ish detail"),
            "incomplete read": http.client.IncompleteRead(b"partial"),
            "disk": PermissionError("no space"),
        }
        for label, error in failures.items():
            def boom(code, error=error):
                raise error

            self.start(boom)
            status, body = self.callback()
            self.assertEqual(status, 502, label)
            self.assertNotIn("<script>", body)
            self.assertNotIn("secret-ish", body)
            self.thread.join(5)
            self.assertFalse(self.thread.is_alive(), label)
            kind, message = self.ctx["done"][0]
            self.assertEqual(kind, "error", label)
            self.assertIn("generate a new key", message, label)
            self.stop()

    def test_a_malformed_code_with_a_good_state_ends_with_an_error_and_no_network(self):
        calls = []
        self.start(lambda code: calls.append(code) or GOOD)
        self.assertEqual(self.callback(code="../x")[0], 502)
        self.thread.join(5)
        self.assertEqual(self.ctx["done"][0][0], "error")
        self.assertEqual(calls, [])

    def test_an_idle_connection_does_not_stall_the_server(self):
        self.start(lambda code: GOOD, conn_timeout=1)
        idle = socket.create_connection(("127.0.0.1", self.port))
        self.addCleanup(idle.close)
        started = time.monotonic()
        self.assertEqual(self.get(f"{self.base}/")[0], 200)
        self.assertLess(time.monotonic() - started, 6)


class Main(unittest.TestCase):
    def test_bad_broker_url_exits_2_before_serving(self):
        self.assertEqual(reg.main(["--broker-url", "not a url", "--out-dir", tempfile.gettempdir(), "--timeout", "0.2"]), 2)

    def test_an_unwritable_out_dir_exits_2_before_serving(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file"
            blocker.write_text("x")
            self.assertEqual(reg.main(["--out-dir", str(blocker), "--timeout", "0.2"]), 2)
            self.assertEqual(reg.main(["--out-dir", str(blocker / "sub"), "--timeout", "0.2"]), 2)

    @unittest.skipIf(os.geteuid() == 0, "root can write to a read-only directory")
    def test_a_read_only_out_dir_exits_2_before_serving(self):
        with tempfile.TemporaryDirectory() as tmp:
            ro = Path(tmp) / "ro"
            ro.mkdir(mode=0o500)
            try:
                self.assertEqual(reg.main(["--out-dir", str(ro), "--timeout", "0.2"]), 2)
            finally:
                os.chmod(ro, 0o700)

    def test_timeout_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(reg.main(["--timeout", "0.2", "--out-dir", tmp]), 1)

    def test_the_whole_run_prints_ids_and_paths_but_no_secret(self):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            result = {}

            def run():
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    result["rc"] = reg.main(["--out-dir", tmp, "--timeout", "20"], do_exchange=lambda code: GOOD)

            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            url = None
            for _ in range(100):
                match = re.search(r"Open (http://127\.0\.0\.1:\d+/[^/ ]+/) in your browser", out.getvalue())
                if match:
                    url = match.group(1)
                    break
                time.sleep(0.05)
            self.assertIsNotNone(url, "the helper never printed its link")
            host = url.split("/")[2]
            path = "/" + url.split("/")[3]
            conn = http.client.HTTPConnection(host, timeout=8)
            conn.request("GET", path + "/")
            form = conn.getresponse().read().decode()
            conn.close()
            state = re.search(r"state=([A-Za-z0-9_-]+)", form).group(1)
            conn = http.client.HTTPConnection(host, timeout=8)
            conn.request("GET", f"{path}/callback?code={CODE}&state={state}")
            self.assertEqual(conn.getresponse().status, 200)
            conn.close()
            thread.join(10)
            self.assertEqual(result["rc"], 0)
            shown = out.getvalue() + err.getvalue()
            for needle in ("id: 4242", "client_id: Iv1.abc", "slug: external-actor", f"{tmp}/external-actor.pem"):
                self.assertIn(needle, shown)
            for secret in ("MIIFAKE", GOOD["webhook_secret"], "CLIENT-SECRET", CODE, state):
                self.assertNotIn(secret, shown)
            self.assertEqual(mode(Path(tmp) / "external-actor.pem"), 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=1)
