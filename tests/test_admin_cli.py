import json

import pytest

from sandick import admin


def _catalog_file(tmp_path):
    data = {
        "dexs": {
            "tradexyz": [
                {"coin": "SNDK", "sz_decimals": 2, "max_leverage": 5},
                {"coin": "ALAB", "sz_decimals": 2, "max_leverage": 5},
                {"coin": "INTC", "sz_decimals": 1, "max_leverage": 10},
            ]
        }
    }
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(data))
    return p


# ---- parse_kv -----------------------------------------------------------

def test_parse_kv_parses_pairs():
    assert admin.parse_kv("SNDK=2, ALAB=1") == {"SNDK": "2", "ALAB": "1"}


def test_parse_kv_empty_is_empty():
    assert admin.parse_kv(None) == {}
    assert admin.parse_kv("") == {}


def test_parse_kv_rejects_malformed():
    with pytest.raises(ValueError):
        admin.parse_kv("SNDK")


# ---- build-basket -------------------------------------------------------

def test_build_basket_from_catalog_writes_file(tmp_path, capsys):
    catalog = _catalog_file(tmp_path)
    out = tmp_path / "out" / "basket.json"
    rc = admin.main(
        [
            "build-basket",
            "--select", "SNDK,ALAB",
            "--dex", "tradexyz",
            "--name", "SANDICK",
            "--catalog", str(catalog),
            "--out", str(out),
        ]
    )
    assert rc == 0
    assert out.exists()  # parent dir was created
    basket = json.loads(out.read_text())
    assert basket["name"] == "SANDICK"
    assert basket["dex"] == "tradexyz"
    assert [a["coin"] for a in basket["assets"]] == ["SNDK", "ALAB"]
    assert "Wrote basket" in capsys.readouterr().out


def test_build_basket_applies_labels_and_weights(tmp_path):
    catalog = _catalog_file(tmp_path)
    out = tmp_path / "basket.json"
    admin.main(
        [
            "build-basket",
            "--select", "SNDK,ALAB",
            "--dex", "tradexyz",
            "--catalog", str(catalog),
            "--label", "SNDK=SanDisk",
            "--weights", "SNDK=2,ALAB=1",
            "--out", str(out),
        ]
    )
    basket = json.loads(out.read_text())
    sndk = next(a for a in basket["assets"] if a["coin"] == "SNDK")
    assert sndk["company"] == "SanDisk"
    assert sndk["weight"] == 2.0


def test_build_basket_with_groups(tmp_path):
    catalog = _catalog_file(tmp_path)
    out = tmp_path / "basket.json"
    admin.main(
        [
            "build-basket",
            "--select", "SNDK,INTC",
            "--dex", "tradexyz",
            "--catalog", str(catalog),
            "--group", "SNDK=storage,INTC=compute",
            "--group-weights", "storage=0.6,compute=0.4",
            "--out", str(out),
        ]
    )
    basket = json.loads(out.read_text())
    assert basket["groups"] == {"storage": 0.6, "compute": 0.4}
    sndk = next(a for a in basket["assets"] if a["coin"] == "SNDK")
    assert sndk["group"] == "storage"


def test_build_basket_empty_selection_errors(tmp_path, capsys):
    catalog = _catalog_file(tmp_path)
    out = tmp_path / "basket.json"
    rc = admin.main(
        ["build-basket", "--select", " , ", "--catalog", str(catalog), "--out", str(out)]
    )
    assert rc == 2
    assert "at least one asset" in capsys.readouterr().err
    assert not out.exists()


def test_build_basket_unknown_asset_errors(tmp_path, capsys):
    catalog = _catalog_file(tmp_path)
    out = tmp_path / "basket.json"
    rc = admin.main(
        ["build-basket", "--select", "NOPE", "--catalog", str(catalog), "--out", str(out)]
    )
    assert rc == 2
    assert "error:" in capsys.readouterr().err


def test_build_basket_bad_catalog_path_errors(tmp_path, capsys):
    out = tmp_path / "basket.json"
    rc = admin.main(
        ["build-basket", "--select", "SNDK", "--catalog", str(tmp_path / "missing.json"), "--out", str(out)]
    )
    assert rc == 2
    assert "could not load catalog" in capsys.readouterr().err


# ---- discover -----------------------------------------------------------

def test_discover_lists_assets_and_saves_snapshot(tmp_path, capsys, install_hyperliquid):
    install_hyperliquid(
        perp_dexs=[None, {"name": "tradexyz"}],
        meta={"universe": [{"name": "SNDK", "szDecimals": 2, "maxLeverage": 5}]},
    )
    out = tmp_path / "snapshot.json"
    rc = admin.main(["discover", "--out", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "SNDK" in text
    assert "assets across" in text
    snapshot = json.loads(out.read_text())
    assert "dexs" in snapshot


def test_discover_handles_failure(tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(admin, "discover_assets", boom)
    rc = admin.main(["discover"])
    assert rc == 2
    assert "discovery failed" in capsys.readouterr().err
