import pytest

from sandick.deploy_config import (
    asset_ids_for,
    core_scale,
    find_perp_dex_index,
    usdc_system_address,
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
