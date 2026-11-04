#!/usr/bin/env python3
"""Offline tests for tests/acceptance_live.py itself: a fake GitHub behind a fake `curl` runs the live
suite. The honest fake must pass; each broken fake (a wrong install, an over-wide App, a read token
that can write, a failing revoke) must make the suite fail, so it cannot pass vacuously against the
real API. SUITE_UNDER_TEST overrides the suite (mutation checks)."""
import os
import unittest
from pathlib import Path

from support import TESTS, APP_ID, Workspace

SUITE = Path(os.environ.get("SUITE_UNDER_TEST", TESTS / "acceptance_live.py"))
LIVE_ENV = {"ACCEPT_LIVE": "1", "GH_APP_ID": APP_ID, "GH_APP_INSTALLATION_ID": "4242"}


class FakeGitHub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace("github_curl.py", LIVE_ENV)

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()

    def run_suite(self, mode="", *args, **kw):
        return self.ws.run(SUITE, *args, env={"FAKE_GH": mode}, **kw)

    def revoked(self):
        return len(list((self.ws.fake / "revoked").iterdir()))

    def assert_clean(self, r, label):
        self.assertFalse((self.ws.fake / "branch").exists(), f"{label}: the suite left the branch behind")
        self.assertEqual(self.revoked(), 3, f"{label}: not all three tokens were revoked")
        self.assertNotIn("ghs_", r.stdout + r.stderr, f"{label}: a token reached the suite's output")

    def test_honest_github_passes_and_cleans_up(self):
        r = self.run_suite()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stderr, r"(?m)^OK$")
        self.assertNotIn("skipped", r.stderr)
        self.assert_clean(r, "honest GitHub")

    def test_a_public_sibling_repo_skips_the_private_check_without_failing(self):
        # the real-world case; jq -e once failed on `false`
        r = self.run_suite("public_other")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipped=1", r.stderr)
        self.assertNotRegex(r.stderr, r"(?m)^(FAIL|ERROR):")
        self.assert_clean(r, "public_other")

    def test_every_broken_github_fails_the_suite(self):
        for mode in ("sel_all", "admin_perm", "read_writes", "revoke_fail"):
            with self.subTest(mode):
                r = self.run_suite(mode)
                self.assertNotEqual(r.returncode, 0, f"{mode}: the suite passed against a broken GitHub")
                self.assertRegex(r.stderr, r"(?m)^(FAIL|ERROR):", f"{mode}: failed without a failure line")

    def test_a_read_token_that_can_write_leaves_no_branch_and_no_live_token(self):
        # -f stops at the first failure (A4), before A5 deletes the branch and A8 and A9 revoke:
        # only the suite's own cleanup can then remove the branch and revoke the tokens
        r = self.run_suite("read_writes", "-f")
        self.assertNotEqual(r.returncode, 0)
        self.assert_clean(r, "read_writes")

    def test_a_failed_mint_still_revokes_the_tokens_already_minted(self):
        # setUpClass raises on the third mint; unittest then skips tearDownClass, class cleanups still run
        r = self.run_suite("mint_fail")
        self.assertNotEqual(r.returncode, 0)
        self.assertRegex(r.stderr, r"(?m)^ERROR:")
        self.assertEqual(self.revoked(), 2, "tokens minted before the failed mint were left live")
        self.assertNotIn("ghs_", r.stdout + r.stderr)

    def test_refuses_to_run_without_the_opt_in(self):
        r = self.ws.run(SUITE, unset=("ACCEPT_LIVE",))
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.ws.fake / "revoked").exists(), "the suite reached GitHub without the opt-in")


if __name__ == "__main__":
    unittest.main()
