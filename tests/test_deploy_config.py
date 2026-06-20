import json

import pytest

from sandick.basket import Basket
from sandick.deploy_config import (
    asset_ids_for,
    build_deploy_config,
    core_scale,
    find_perp_dex_index,
    usdc_system_address,
    write_deploy_config,
)


def _basket():
    return Basket.from_dict(
        {
            "name": "SANDICK",
            "dex": "tradexyz",
            "assets": [
                {"company": "S", "ticker": "SNDK", "coin": "SNDK", "sz_decimals": 2},
                {"company": "I", "ticker": "INTC", "coin": "INTC", "sz_decimals": 1},
            ],
        }
    )


def test_usdc_system_address_format():
    # First byte 0x20, token index big-endian in the low bytes (docs example: 200 -> ..c8).
    assert usdc_system_address(200) == "0x20000000000000000000000000000000000000c8"
    assert usdc_system_address(0) == "0x2000000000000000000000000000000000000000"
    assert len(usdc_system_address(5)) == 42


def test_core_scale():
    assert core_scale(6, 6) == 1       # same decimals
    assert core_scale(6, 8) == 100     # core has 2 more
    with pytest.raises(ValueError):
        core_scale(8, 6)               # negative diff needs explicit handling


def test_asset_ids_for():
    universe = ["SNDK", "ALAB", "NBIS"]
    ids = asset_ids_for(["NBIS", "SNDK"], universe, perp_dex_index=1)
    assert ids == {"NBIS": 110002, "SNDK": 110000}


def test_asset_ids_for_missing_coin():
    with pytest.raises(KeyError):
        asset_ids_for(["ZZZ"], ["SNDK"], perp_dex_index=1)


def test_find_perp_dex_index_by_name_and_deployer():
    dexs = [None, {"name": "foo", "deployer": "0xAbC"}, {"name": "tradexyz", "deployer": "0xDEf"}]
    assert find_perp_dex_index(dexs, name="tradexyz") == 2
    assert find_perp_dex_index(dexs, deployer="0xdef") == 2
    with pytest.raises(KeyError):
        find_perp_dex_index(dexs, name="missing")


def test_usdc_system_address_rejects_negative():
    with pytest.raises(ValueError):
        usdc_system_address(-1)


def test_build_deploy_config_assembles_from_live_data(install_hyperliquid):
    install_hyperliquid(
        perp_dexs=[None, {"name": "tradexyz", "deployer": "0xabc"}],
        meta={"universe": [{"name": "SNDK"}, {"name": "INTC"}]},
        spot_meta={"tokens": [{"name": "USDC", "index": 0, "weiDecimals": 8}]},
    )
    cfg = build_deploy_config(_basket(), testnet=True)
    assert cfg["network"] == "testnet"
    assert cfg["basket"] == "SANDICK"
    assert cfg["dex"] == "tradexyz"
    assert cfg["perpDexIndex"] == 1
    assert cfg["assetIds"] == {"SNDK": 110000, "INTC": 110001}
    assert cfg["usdcCoreTokenIndex"] == 0
    assert cfg["coreScale"] == 100  # weiDecimals 8 - evm 6
    assert cfg["tif"] == 3


def test_write_deploy_config_roundtrips(tmp_path):
    cfg = {"network": "testnet", "perpDexIndex": 1, "assetIds": {"SNDK": 110000}}
    out = tmp_path / "deploy.json"
    write_deploy_config(cfg, str(out))
    assert json.loads(out.read_text()) == cfg
    assert out.read_text().endswith("\n")


def test_main_writes_config_from_live_data(tmp_path, capsys, install_hyperliquid):
    from sandick.deploy_config import _main

    basket_path = tmp_path / "basket.json"
    basket_path.write_text(
        json.dumps(
            {
                "name": "SANDICK",
                "dex": "tradexyz",
                "assets": [
                    {"company": "S", "ticker": "SNDK", "coin": "SNDK", "sz_decimals": 2},
                ],
            }
        )
    )
    install_hyperliquid(
        perp_dexs=[None, {"name": "tradexyz", "deployer": "0xabc"}],
        meta={"universe": [{"name": "SNDK"}]},
        spot_meta={"tokens": [{"name": "USDC", "index": 0, "weiDecimals": 8}]},
    )
    out = tmp_path / "deploy.json"
    rc = _main(["--basket", str(basket_path), "--out", str(out)])
    assert rc == 0
    cfg = json.loads(out.read_text())
    assert cfg["perpDexIndex"] == 1
    assert cfg["assetIds"] == {"SNDK": 110000}
    assert "Wrote" in capsys.readouterr().out
