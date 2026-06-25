import pytest

from sandick.basket import DEFAULT_BASKET_PATH, Basket, BasketAsset


def test_default_basket_loads_and_spells_sandick():
    basket = Basket.load(DEFAULT_BASKET_PATH)
    assert basket.name == "SANDICK"
    assert len(basket.assets) == 7
    # The seven slots map to the letters S-A-N-D-I-C-K. Every company's initial
    # matches its letter (the K slot is Kioxia), so the basket spells SANDICK
    # exactly.
    initials = [a.company[0].upper() for a in basket.assets]
    assert initials == list("SANDICK")
    assert basket.assets[6].company == "Kioxia"


def test_duplicate_coins_rejected():
    with pytest.raises(ValueError):
        Basket.from_dict(
            {
                "assets": [
                    {"company": "X", "ticker": "X", "coin": "DUP", "sz_decimals": 2},
                    {"company": "Y", "ticker": "Y", "coin": "DUP", "sz_decimals": 2},
                ]
            }
        )


def test_empty_basket_rejected():
    with pytest.raises(ValueError):
        Basket.from_dict({"assets": []})


def test_asset_defaults_sz_decimals():
    a = BasketAsset.from_dict({"company": "X", "ticker": "X", "coin": "X"})
    assert a.sz_decimals == 2
