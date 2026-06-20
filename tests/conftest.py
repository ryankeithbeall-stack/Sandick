"""Shared test fixtures.

The live code paths import the ``hyperliquid`` SDK lazily, inside the functions
that need it, and only talk to the network through a small ``Info`` surface
(``all_mids`` / ``meta`` / ``user_state`` / ``post`` / ``spot_meta``). That lets
us exercise those otherwise network-bound paths offline by installing a fake
``hyperliquid`` package into ``sys.modules`` with canned responses.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture
def install_hyperliquid(monkeypatch):
    """Install a fake ``hyperliquid`` SDK and return the FakeInfo class.

    Pass canned responses as keyword args; each maps to the matching ``Info``
    method. ``perp_dexs`` answers ``post('/info', {'type': 'perpDexs'})``.
    Instances record their constructor args and calls on class attributes so a
    test can assert how the SDK was driven.
    """

    def _install(
        *,
        mids=None,
        meta=None,
        user_state=None,
        perp_dexs=None,
        spot_meta=None,
    ):
        class FakeInfo:
            instances = []

            def __init__(self, base_url, skip_ws=True):
                self.base_url = base_url
                self.skip_ws = skip_ws
                self.calls = []
                FakeInfo.instances.append(self)

            def all_mids(self, dex=""):
                self.calls.append(("all_mids", dex))
                return dict(mids or {})

            def meta(self, dex=""):
                self.calls.append(("meta", dex))
                if isinstance(meta, Exception):
                    raise meta
                return dict(meta or {})

            def user_state(self, address, dex=""):
                self.calls.append(("user_state", address, dex))
                if isinstance(user_state, Exception):
                    raise user_state
                return dict(user_state or {})

            def spot_meta(self):
                self.calls.append(("spot_meta",))
                return dict(spot_meta or {})

            def post(self, path, body):
                self.calls.append(("post", path, body))
                if body.get("type") == "perpDexs":
                    return list(perp_dexs or [])
                raise AssertionError(f"unexpected post body: {body!r}")

        info_mod = types.ModuleType("hyperliquid.info")
        info_mod.Info = FakeInfo

        constants = types.SimpleNamespace(
            MAINNET_API_URL="https://api.example/mainnet",
            TESTNET_API_URL="https://api.example/testnet",
        )
        utils_mod = types.ModuleType("hyperliquid.utils")
        utils_mod.constants = constants

        pkg = types.ModuleType("hyperliquid")
        pkg.info = info_mod
        pkg.utils = utils_mod

        monkeypatch.setitem(sys.modules, "hyperliquid", pkg)
        monkeypatch.setitem(sys.modules, "hyperliquid.info", info_mod)
        monkeypatch.setitem(sys.modules, "hyperliquid.utils", utils_mod)
        return FakeInfo

    return _install
