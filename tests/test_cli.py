"""Tests untuk CLI simpel: parse_args + broker leverage/margin-type."""
import pytest

from scalping.__main__ import parse_args
from scalping.broker import PaperBroker


def test_parse_flag_style():
    a = parse_args(["HBARUSDT", "--margin", "100", "--lev", "10",
                    "--mode", "paper", "--margin-mode", "isolated"])
    assert a["symbol"] == "HBARUSDT"
    assert a["margin"] == 100.0
    assert a["lev"] == 10
    assert a["mode"] == "paper"
    assert a["margin_mode"] == "isolated"


def test_parse_positional_style_like_user():
    # contoh user: --margin 100 --lev 10 isolated paper HBARUSDT
    a = parse_args(["--margin", "100", "--lev", "10",
                    "isolated", "paper", "HBARUSDT"])
    assert a["symbol"] == "HBARUSDT"
    assert a["margin"] == 100.0
    assert a["lev"] == 10
    assert a["mode"] == "paper"
    assert a["margin_mode"] == "isolated"


def test_parse_cross_and_testnet():
    a = parse_args(["cross", "testnet", "BTCUSDT"])
    assert a["margin_mode"] == "cross"
    assert a["mode"] == "testnet"
    assert a["symbol"] == "BTCUSDT"


def test_parse_defaults():
    a = parse_args([])
    assert a["symbol"] == "BTCUSDT"
    assert a["margin"] == 100.0
    assert a["lev"] == 10
    assert a["margin_mode"] == "isolated"


def test_parse_invalid():
    with pytest.raises(SystemExit):
        parse_args(["--lev", "200"])
    with pytest.raises(SystemExit):
        parse_args(["--margin", "0"])


def test_paper_broker_leverage_margin():
    b = PaperBroker()
    r1 = b.set_leverage("BTCUSDT", 10)
    assert r1["leverage"] == 10 and r1["simulated"]
    r2 = b.set_margin_type("BTCUSDT", "isolated")
    assert r2["marginType"] == "ISOLATED"
    with pytest.raises(ValueError):
        b.set_margin_type("BTCUSDT", "ngawur")
