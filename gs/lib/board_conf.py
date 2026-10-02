"""Minimal reader of the board profile (gs/boards/<id>/board.conf) for Python scripts.

Python cannot source the shell libs, so this parses the KEY='value' lines of board.conf the same way
gs/lib/board.sh resolves them: BOARD env (default radxa-zero3), /gs/boards first, then ../boards next to this file.
get() never raises: a missing board/file/key or an empty value returns the given default (the Radxa literal).
"""
import os
import re

_LINE = re.compile(r"""^([A-Z][A-Z0-9_]*)=('([^']*)'|"([^"$`]*)"|([A-Za-z0-9_./:@%+-]*))\s*(#.*)?$""")


def parse(path):
    """Return {KEY: value} for the plain KEY=value lines of a board.conf; other lines are ignored."""
    out = {}
    try:
        with open(path, "r") as f:
            for line in f:
                m = _LINE.match(line.strip())
                if not m:
                    continue
                if m.group(3) is not None:
                    val = m.group(3)
                elif m.group(4) is not None:
                    val = m.group(4)
                else:
                    val = m.group(5)
                out[m.group(1)] = val
    except OSError:
        return {}
    return out


def find_conf(board=None, dirs=None):
    """Locate boards/<board>/board.conf; return its path or None."""
    board = board or os.environ.get("BOARD") or "radxa-zero3"
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (dirs if dirs is not None else ["/gs/boards", os.path.join(here, "..", "boards")]):
        p = os.path.join(d, board, "board.conf")
        if os.path.isfile(p):
            return p
    return None


def get(key, default, board=None, dirs=None):
    """Value of KEY from the active board profile, or default if unavailable or empty."""
    p = find_conf(board, dirs)
    if p is None:
        return default
    return parse(p).get(key) or default
