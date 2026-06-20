import pytest

from sandick.admin import (
    basket_dict_from_assets,
    catalog_from_json,
    catalog_to_json,
    resolve_selection,
)
from sandick.discovery import AssetInfo, flatten, parse_meta_universe, parse_perp_dexs


def test_parse_perp_dexs_maps_null_to_core():
    assert parse_perp_dexs([None, {"name": "sandick"}, {"name": "foo"}]) == ["", "sandick", "foo"]


def test_parse_meta_universe_skips_delisted_and_bad():
    meta = {
        "universe": [
            {"name": "SNDK", "szDecimals": 2, "maxLeverage": 5},
            {"name": "DEAD", "szDecimals": 2, "isDelisted": True},
            {"szDecimals": 2},  # no name -> skipped
        ]
    }
    assets = parse_meta_universe(meta, dex="sandick")
    assert [a.coin for a in assets] == ["SNDK"]
    assert assets[0].dex == "sandick"
    assert assets[0].max_leverage == 5


def test_qualified_name():
    assert AssetInfo("", "INTC", 1).qualified == "INTC"
    assert AssetInfo("sandick", "SNDK", 2).qualified == "sandick:SNDK"


def _catalog():
    return {
        "sandick": [
            AssetInfo("sandick", "SNDK", 2, 5),
            AssetInfo("sandick", "ALAB", 2, 5),
        ],
        "": [AssetInfo("", "ALAB", 1, 20)],  # ALAB also on core dex -> ambiguous
    }


def test_resolve_selection_qualified():
    assets = resolve_selection(_catalog(), ["sandick:SNDK"])
    assert assets[0].coin == "SNDK" and assets[0].dex == "sandick"


def test_resolve_selection_ambiguous_raises():
    with pytest.raises(ValueError):
        resolve_selection(_catalog(), ["ALAB"])


def test_resolve_selection_dex_hint_disambiguates():
    assets = resolve_selection(_catalog(), ["ALAB"], dex_hint="sandick")
    assert assets[0].dex == "sandick"


def test_resolve_selection_missing_raises():
    with pytest.raises(KeyError):
        resolve_selection(_catalog(), ["NOPE"])


def test_catalog_json_roundtrip():
    cat = _catalog()
    back = catalog_from_json(catalog_to_json(cat))
    assert flatten(back) == flatten(cat)


def test_basket_dict_from_assets_shape():
    assets = [AssetInfo("sandick", "SNDK", 2, 5), AssetInfo("sandick", "ALAB", 2, 5)]
    d = basket_dict_from_assets(assets, name="SANDICK", dex="sandick")
    assert d["name"] == "SANDICK"
    assert d["dex"] == "sandick"
    assert [a["coin"] for a in d["assets"]] == ["SNDK", "ALAB"]
