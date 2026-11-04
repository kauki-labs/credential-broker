#!/usr/bin/env python3
"""Fake curl: record argv, the header fed on stdin and any `-d @file` body, answer from the env."""
import json
import os
import stat
import sys

d = os.environ["FAKE_DIR"]
argv = sys.argv[1:]
with open(f"{d}/argv", "w") as f:
    json.dump(argv, f)
if "@-" in argv:
    with open(f"{d}/stdin", "w") as f:
        f.write(sys.stdin.read())
for a in argv:
    if a.startswith("@") and a != "@-":
        with open(a[1:]) as src, open(f"{d}/body", "w") as dst:
            dst.write(src.read())
        with open(f"{d}/bodymode", "w") as dst:
            dst.write(format(stat.S_IMODE(os.stat(a[1:]).st_mode), "o"))
open(f"{d}/called", "w").close()
sys.stdout.write(os.environ.get("FAKE_RESPONSE", ""))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
