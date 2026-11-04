"""Shared helpers for the offline suites. A fake `curl` goes first on PATH, so the shell scripts
under test never reach GitHub. The scripts run as black boxes in a minimal, explicit environment."""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FAKES = TESTS / "fakes"
APP_ID = "Iv1.abc123"


def b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def openssl(*args, input=None):
    return subprocess.run(["openssl", *args], check=True, capture_output=True, input=input)


def make_keypair(directory, name):
    priv, pub = directory / f"{name}.pem", directory / f"{name}-pub.pem"
    openssl("genrsa", "-out", str(priv), "2048")
    openssl("rsa", "-in", str(priv), "-pubout", "-out", str(pub))
    os.chmod(priv, 0o600)
    return priv, pub


def decode_jwt(jwt):
    """Return (header, claims, signing input, signature) of a JWT, or fail on a bad shape."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", jwt):
        raise ValueError("not base64url without padding")
    h, p, s = jwt.split(".")
    return json.loads(b64url_decode(h)), json.loads(b64url_decode(p)), f"{h}.{p}".encode(), b64url_decode(s)


def verifies(pub, signing_input, signature, directory):
    sig = Path(directory) / "sig.bin"
    sig.write_bytes(signature)
    r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(sig)],
                       input=signing_input, capture_output=True)
    return r.returncode == 0


def write_private(path, text, mode=0o600):
    path = Path(path)
    path.write_text(text)
    os.chmod(path, mode)
    return path


class Workspace:
    """A private directory with two key pairs, a bin/ holding the fake curl, and a TMPDIR the
    script under test must leave empty."""

    def __init__(self, fake, base_env):
        self.dir = Path(tempfile.mkdtemp(prefix="broker-test-"))
        self.bin = self.dir / "bin"
        self.tmpdir = self.dir / "tmpdir"
        self.fake = self.dir / "fake"
        for d in (self.bin, self.tmpdir, self.fake):
            d.mkdir()
        shutil.copy(FAKES / fake, self.bin / "curl")
        os.chmod(self.bin / "curl", 0o755)
        self.key, self.pub = make_keypair(self.dir, "key")
        self.other_key, self.other_pub = make_keypair(self.dir, "other")
        self.base_env = {k: str(v).replace("{ws}", str(self.dir)) for k, v in base_env.items()}
        self.base_env["GH_APP_KEY_FILE"] = str(self.key)

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def env(self, extra=None, unset=()):
        e = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.dir),
             "TMPDIR": str(self.tmpdir), "FAKE_DIR": str(self.fake), **self.base_env}
        e.update({k: str(v) for k, v in (extra or {}).items()})
        for k in unset:
            e.pop(k, None)
        return e

    def reset(self):
        for f in self.fake.iterdir():
            if f.is_file():
                f.unlink()
            else:
                shutil.rmtree(f)

    def run(self, tool, *args, env=None, unset=(), pass_fds=(), reset=True):
        if reset:
            self.reset()
        return subprocess.run([str(tool), *args], env=self.env(env, unset), capture_output=True,
                              text=True, pass_fds=pass_fds)

    # what the fake curl recorded
    def called(self):
        return (self.fake / "called").exists()

    def argv(self):
        return json.loads((self.fake / "argv").read_text())

    def stdin(self):
        return (self.fake / "stdin").read_text()

    def jwt_sent(self):
        m = re.search(r"^Authorization: Bearer (\S+)$", self.stdin(), re.M)
        return m.group(1) if m else ""

    def body_after(self, flag):
        a = self.argv()
        return a[a.index(flag) + 1]

    def tmpdir_files(self):
        return sorted(p.name for p in self.tmpdir.iterdir())
