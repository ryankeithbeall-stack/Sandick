import pytest

from sandick.basket import Basket, BasketAsset, DEFAULT_BASKET_PATH


def test_default_basket_loads_and_spells_sandick():
    basket = Basket.load(DEFAULT_BASKET_PATH)
    assert basket.name == "SANDICK"
    assert len(basket.assets) == 7
    initials = "".join(a.ticker[0] for a in basket.assets)
    # S-A-N-D-I-C-K from the tickers' first letters (Kioxia -> ticker 285A
    # is the odd one out, so check companies instead for the K).
    companies_initials = "".join(a.company[0] for a in basket.assets).upper()
    assert companies_initials == "SANDICK"


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
