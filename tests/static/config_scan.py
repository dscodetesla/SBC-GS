#!/usr/bin/env python3
"""Hardcode scanner for the ratchet in tests/static/config.sh: counts numeric literals, ports, IPv4 addresses and absolute
device/runtime paths that are NOT read from config/registry.tsv, per file. Output: 'file count' lines (sorted) and 'total N'.
  python3 tests/static/config_scan.py [--list]     --list prints every finding as file:line text

Rules (docs/CONFIG.md, section 'храповик'):
  Python: ast constants. Numbers 0, 1, 2 (and 0.0/1.0/2.0) are structural and ignored; every other int/float counts.
          Strings count when they hold an IPv4 address, a ':PORT' (4-5 digits) or a /dev|/run|/tmp|/var|/etc|/config|/opt path.
          Docstrings are ignored.
  Shell : comments and ANSI colour codes are stripped; positional parameters ($1, ${#2}) are ignored; counted are IPv4
          addresses, decimals (1.5) and integers with 2+ digits that are not part of an identifier (wlan0, x264enc, h265).
  A line containing 'cfg-ok' is deliberately exempt (protocol constants such as the 8 RC channels or 65535 = 'ignore').
"""
import ast
import glob
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FILES = (sorted(glob.glob("bench/*.sh")) + sorted(glob.glob("bench/*.py")) + ["gs/mavlink/gs-mavlink.sh"]
         + [f for f in sorted(glob.glob("tests/sim/*.py")) if not os.path.basename(f).startswith("test_")]
         + sorted(glob.glob("tests/sim/*.sh")) + ["build/release.sh"])
TRIVIAL = {0, 1, 2}
STR_PAT = re.compile(r"(\b\d{1,3}(\.\d{1,3}){3}\b)|(:\d{4,5}\b)|(^/(dev|run|tmp|var|etc|config|opt)(/|$))")


def scan_py(path, src):
    tree = ast.parse(src)
    lines = src.split("\n")
    doc = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant):
            doc.add(id(n.body[0].value))
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Constant) or id(n) in doc or isinstance(n.value, bool):
            continue
        line = lines[n.lineno - 1]
        if "cfg-ok" in line:
            continue
        v = n.value
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if v not in TRIVIAL:
                out.append((n.lineno, repr(v)))
        elif isinstance(v, str) and STR_PAT.search(v):
            out.append((n.lineno, repr(v[:50])))
    return out


NUM = re.compile(r"(?<![A-Za-z_0-9$#.{\-])(\d+(?:\.\d+){3}|\d+\.\d+|\d{2,})(?![A-Za-z_0-9])")


def scan_sh(path, src):
    out = []
    for i, line in enumerate(src.split("\n"), 1):
        if line.lstrip().startswith("#") or "cfg-ok" in line:
            continue
        line = re.sub(r"\s#\s.*$", "", line)
        line = re.sub(r"\\0\d\d\[[0-9;]*m", "", line)
        line = re.sub(r"\$\{?#?\d+\}?", "", line)
        for m in NUM.finditer(line):
            out.append((i, m.group(1)))
    return out


def main():
    listing = "--list" in sys.argv
    total = 0
    rows = []
    for f in FILES:
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p):
            continue
        src = open(p, encoding="utf-8").read()
        found = scan_py(f, src) if f.endswith(".py") else scan_sh(f, src)
        rows.append((f, len(found)))
        total += len(found)
        if listing:
            for ln, txt in sorted(found):
                print(f"{f}:{ln} {txt}")
    if not listing:
        for f, n in rows:
            print(f"{f} {n}")
        print(f"total {total}")


if __name__ == "__main__":
    main()
