"""Locates and loads config/load.py (layered configuration, docs/CONFIG.md) for the tests/sim tools."""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def lib():
    p = os.path.join(_HERE, "..", "..", "config", "load.py")
    spec = importlib.util.spec_from_file_location("sbc_gs_load", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load(owners, argv=None):
    return lib().load(owners, argv=argv)
