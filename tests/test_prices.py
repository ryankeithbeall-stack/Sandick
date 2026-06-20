import json

import pytest

from sandick.prices import fetch_live_prices, load_prices_file


def test_load_prices_file_parses_and_coerces_floats(tmp_path):
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({"SNDK": "50.5", "INTC": 22}))
    prices = load_prices_file(p)
    assert prices == {"SNDK": 50.5, "INTC": 22.0}
    assert all(isinstance(v, float) for v in prices.values())


def test_load_prices_file_drops_underscore_metadata(tmp_path):
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({"_comment": "ignore me", "_source": "x", "SNDK": 50.0}))
    prices = load_prices_file(p)
    assert prices == {"SNDK": 50.0}


def test_load_prices_file_accepts_str_path(tmp_path):
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({"A": 1.0}))
    assert load_prices_file(str(p)) == {"A": 1.0}


def test_fetch_live_prices_returns_requested_coins(install_hyperliquid):
    FakeInfo = install_hyperliquid(mids={"SNDK": "50.0", "INTC": "22.0", "OTHER": "1.0"})
    prices = fetch_live_prices(["SNDK", "INTC"], dex="tradexyz", mainnet=True)
    assert prices == {"SNDK": 50.0, "INTC": 22.0}
    # mainnet base url was used and the dex was passed through to all_mids.
    info = FakeInfo.instances[-1]
    assert info.base_url.endswith("mainnet")
    assert ("all_mids", "tradexyz") in info.calls


def test_fetch_live_prices_testnet_uses_testnet_url(install_hyperliquid):
    FakeInfo = install_hyperliquid(mids={"SNDK": "50.0"})
    fetch_live_prices(["SNDK"], mainnet=False)
    assert FakeInfo.instances[-1].base_url.endswith("testnet")


def test_fetch_live_prices_missing_coin_raises(install_hyperliquid):
    install_hyperliquid(mids={"SNDK": "50.0"})
    with pytest.raises(KeyError):
        fetch_live_prices(["SNDK", "NOPE"], dex="tradexyz")
