# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Tests untuk multi-exchange: factory, normalisasi, mock HTTP."""
import json
import urllib.request

import pytest

from scalping.exchanges import get_exchange, SUPPORTED
from scalping.exchanges.base import norm_symbol_usdt, _norm_margin_type
from scalping.exchanges.okx import to_inst_id, OKXExchangeData
from scalping.exchanges.bybit import BybitExchangeData
from scalping.exchanges.bitget import BitgetExchangeData


def test_supported():
    assert set(SUPPORTED) == {"binance", "okx", "bybit", "bitget"}


def test_factory_default():
    ex = get_exchange(None)
    assert ex["name"] == "binance"
    ex2 = get_exchange("OKX")
    assert ex2["name"] == "okx"


def test_factory_invalid():
    with pytest.raises(ValueError):
        get_exchange("ngawur")


def test_norm_symbol():
    assert norm_symbol_usdt("btcusdt") == "BTCUSDT"
    assert norm_symbol_usdt("BTC-USDT") == "BTCUSDT"
    assert norm_symbol_usdt("eth") == "ETHUSDT"


def test_okx_inst_id():
    assert to_inst_id("BTCUSDT") == "BTC-USDT-SWAP"
    assert to_inst_id("hbar") == "HBAR-USDT-SWAP"


def test_norm_margin():
    assert _norm_margin_type("cross") == "CROSSED"
    assert _norm_margin_type("ISOLATED") == "ISOLATED"
    with pytest.raises(ValueError):
        _norm_margin_type("ngawur")


class _FakeResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock_urlopen(payload):
    def fake(req, timeout=15):
        return _FakeResp(payload)
    return fake


def test_okx_klines_parse(monkeypatch):
    # OKX: newest-first -> dibalik, candle berjalan dibuang
    payload = {"code": "0", "data": [
        ["1700000060000", "101", "102", "100", "101.5", "10", "0", "0", "1"],
        ["1700000000000", "100", "101", "99", "100.5", "11", "0", "0", "1"],
    ]}
    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen(payload))
    kl = OKXExchangeData().get_klines("BTCUSDT", "15m", limit=10)
    assert len(kl) == 1  # 2 baris - 1 berjalan
    assert kl[0]["close"] == 100.5
    assert kl[0]["open_time"] == 1700000000000


def test_bybit_klines_parse(monkeypatch):
    payload = {"retCode": 0, "result": {"list": [
        ["1700000060", "101", "102", "100", "101.5", "10", "1000"],
        ["1700000000", "100", "101", "99", "100.5", "11", "1100"],
    ]}}
    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen(payload))
    kl = BybitExchangeData().get_klines("BTCUSDT", "15m", limit=10)
    assert len(kl) == 1
    assert kl[0]["close"] == 100.5


def test_bitget_klines_parse(monkeypatch):
    payload = {"code": "00000", "data": [
        ["1700000060000", "101", "102", "100", "101.5", "10", "1000"],
        ["1700000000000", "100", "101", "99", "100.5", "11", "1100"],
    ]}
    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen(payload))
    kl = BitgetExchangeData().get_klines("BTCUSDT", "15m", limit=10)
    assert len(kl) == 1
    assert kl[0]["close"] == 100.5


def test_parse_args_exchange():
    from scalping.__main__ import parse_args
    a = parse_args(["BTCUSDT", "-e", "bybit"])
    assert a["exchange"] == "bybit"
    a2 = parse_args([])
    assert a2["exchange"] == "binance"
    with pytest.raises(SystemExit):
        parse_args(["-e", "ngawur"])
