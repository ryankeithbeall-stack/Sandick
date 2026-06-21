"""Guard against drift between the front end and the canonical config.

``frontend/app.js`` hand-mirrors ``config/sandick.basket.json`` and
``config/prices.example.json`` (the zero-build front end can't import them under
``file://``). These tests parse the JS constants and assert they still match the
JSON source of truth, so a change to the config that isn't reflected in the front
end fails CI instead of silently diverging.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "frontend" / "app.js").read_text()
BASKET_JSON = json.loads((ROOT / "config" / "sandick.basket.json").read_text())
PRICES_JSON = json.loads((ROOT / "config" / "prices.example.json").read_text())


def _js_block(name: str) -> str:
    """Return the text of the JS object literal assigned to ``const <name>``."""
    start = APP_JS.index(f"const {name}")
    brace = APP_JS.index("{", start)
    depth = 0
    for i in range(brace, len(APP_JS)):
        if APP_JS[i] == "{":
            depth += 1
        elif APP_JS[i] == "}":
            depth -= 1
            if depth == 0:
                return APP_JS[brace : i + 1]
    raise AssertionError(f"unterminated object literal for {name}")


def _parse_basket() -> dict:
    block = _js_block("BASKET")
    name = re.search(r"name:\s*'([^']*)'", block).group(1)
    dex = re.search(r"dex:\s*'([^']*)'", block).group(1)
    assets = []
    for row in re.finditer(
        r"\{\s*company:\s*'([^']*)',\s*ticker:\s*'([^']*)',\s*"
        r"coin:\s*'([^']*)',\s*sz_decimals:\s*(\d+)\s*\}",
        block,
    ):
        assets.append(
            {
                "company": row.group(1),
                "ticker": row.group(2),
                "coin": row.group(3),
                "sz_decimals": int(row.group(4)),
            }
        )
    return {"name": name, "dex": dex, "assets": assets}


def _parse_prices() -> dict:
    block = _js_block("EXAMPLE_PRICES")
    return {
        m.group(1): float(m.group(2))
        for m in re.finditer(r"(\w+):\s*([\d.]+)", block)
    }


def test_frontend_basket_matches_config():
    js = _parse_basket()
    assert js["name"] == BASKET_JSON["name"]
    assert js["dex"] == BASKET_JSON["dex"]

    js_assets = {a["coin"]: a for a in js["assets"]}
    cfg_assets = {a["coin"]: a for a in BASKET_JSON["assets"]}
    assert js_assets.keys() == cfg_assets.keys(), "front end and config list different coins"
    for coin, cfg in cfg_assets.items():
        got = js_assets[coin]
        assert got["company"] == cfg["company"], coin
        assert got["ticker"] == cfg["ticker"], coin
        assert got["sz_decimals"] == cfg["sz_decimals"], coin


def test_frontend_example_prices_match_config():
    js_prices = _parse_prices()
    cfg_prices = {k: v for k, v in PRICES_JSON.items() if not k.startswith("_")}
    assert js_prices == cfg_prices
