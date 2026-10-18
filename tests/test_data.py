# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Test data.py dengan urllib yang di-mock — TANPA network asli."""

import io
import json
import unittest
from unittest import mock

import urllib.error

from scalping import data as data_mod
from scalping.config import BINANCE_FAPI, BINANCE_TESTNET
from scalping.data import BinanceData, normalize_symbol


def _kline_row(open_time, o, h, l, c, v, close_time):
    # format Binance: string untuk harga/volume
    return [
        open_time, str(o), str(h), str(l), str(c), str(v), close_time,
        "0.0", 0, "0.0", "0.0", "0",
    ]


class _FakeHTTPResponse:
    """Pengganti context manager urlopen."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_urlopen_factory(payload: bytes, capture: dict | None = None):
    def _fake(url_or_request, timeout=None):
        if capture is not None:
            req = url_or_request
            capture["url"] = req.full_url if hasattr(req, "full_url") else req
            capture["timeout"] = timeout
        return _FakeHTTPResponse(payload)

    return _fake


def _sample_rows():
    # 3 candle closed + 1 candle yang masih berjalan (terakhir)
    return [
        _kline_row(1000, 100, 102, 99, 101, 10, 1999),
        _kline_row(2000, 101, 103, 100, 102, 12, 2999),
        _kline_row(3000, 102, 104, 101, 103, 14, 3999),
        _kline_row(4000, 103, 105, 102, 104, 16, 4999),  # forming -> dibuang
    ]


class TestGetKlines(unittest.TestCase):
    def test_candle_closed_saja_oldest_first(self):
        payload = json.dumps(_sample_rows()).encode()
        capture: dict = {}
        with mock.patch(
            "urllib.request.urlopen", _fake_urlopen_factory(payload, capture)
        ):
            candles = BinanceData().get_klines("BTCUSDT", "1m", limit=4)

        # candle forming (terakhir) dibuang -> 3 candle
        self.assertEqual(len(candles), 3)
        # oldest-first
        self.assertEqual(
            [c["open_time"] for c in candles], [1000.0, 2000.0, 3000.0]
        )
        # kunci lengkap & semua float
        for c in candles:
            self.assertEqual(
                set(c.keys()),
                {"open_time", "open", "high", "low", "close",
                 "volume", "close_time"},
            )
            for v in c.values():
                self.assertIsInstance(v, float)
        # nilai candle pertama benar
        first = candles[0]
        self.assertEqual(
            (first["open"], first["high"], first["low"],
             first["close"], first["volume"]),
            (100.0, 102.0, 99.0, 101.0, 10.0),
        )
        # URL memakai base fapi + parameter benar
        self.assertTrue(capture["url"].startswith(BINANCE_FAPI + "/fapi/v1/klines?"))
        self.assertIn("symbol=BTCUSDT", capture["url"])
        self.assertIn("interval=1m", capture["url"])
        self.assertIn("limit=4", capture["url"])
        self.assertEqual(capture["timeout"], 10.0)

    def test_testnet_base_url_dan_timeout_custom(self):
        payload = json.dumps(_sample_rows()).encode()
        capture: dict = {}
        with mock.patch(
            "urllib.request.urlopen", _fake_urlopen_factory(payload, capture)
        ):
            client = BinanceData(testnet=True, timeout=5.0)
            client.get_klines("btc", "5m")
        self.assertEqual(client.base_url, BINANCE_TESTNET)
        self.assertTrue(capture["url"].startswith(BINANCE_TESTNET))
        self.assertEqual(capture["timeout"], 5.0)
        # default: fapi
        self.assertEqual(BinanceData().base_url, BINANCE_FAPI)

    def test_fail_closed_network_error(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with self.assertRaises(RuntimeError):
                BinanceData().get_klines("BTCUSDT", "1m")

    def test_fail_closed_http_error(self):
        err = urllib.error.HTTPError(
            url="http://x", code=451, msg="banned", hdrs=None,
            fp=io.BytesIO(b"{}"),
        )
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(RuntimeError):
                BinanceData().get_klines("BTCUSDT", "1m")

    def test_fail_closed_respons_bukan_json(self):
        with mock.patch(
            "urllib.request.urlopen",
            _fake_urlopen_factory(b"<html>bukan json</html>"),
        ):
            with self.assertRaises(RuntimeError):
                BinanceData().get_klines("BTCUSDT", "1m")

    def test_fail_closed_respons_bukan_list(self):
        payload = json.dumps({"code": -1121, "msg": "bad symbol"}).encode()
        with mock.patch(
            "urllib.request.urlopen", _fake_urlopen_factory(payload)
        ):
            with self.assertRaises(RuntimeError):
                BinanceData().get_klines("BTCUSDT", "1m")

    def test_fail_closed_baris_rusak(self):
        payload = json.dumps([["oops"], _sample_rows()[0]]).encode()
        with mock.patch(
            "urllib.request.urlopen", _fake_urlopen_factory(payload)
        ):
            with self.assertRaises(RuntimeError):
                BinanceData().get_klines("BTCUSDT", "1m")

    def test_interval_tidak_valid(self):
        for bad in ("3m", "1h", "", "1M"):
            with self.assertRaises(ValueError, msg=f"interval={bad!r}"):
                BinanceData().get_klines("BTCUSDT", bad)

    def test_limit_tidak_valid(self):
        for bad in (0, -5, 1501, True):
            with self.assertRaises(ValueError, msg=f"limit={bad!r}"):
                BinanceData().get_klines("BTCUSDT", "1m", limit=bad)

    def test_timeout_tidak_valid(self):
        for bad in (0, -1.0):
            with self.assertRaises(ValueError, msg=f"timeout={bad!r}"):
                BinanceData(timeout=bad)


class TestNormalizeSymbol(unittest.TestCase):
    def test_varian_input(self):
        self.assertEqual(normalize_symbol("btc"), "BTCUSDT")
        self.assertEqual(normalize_symbol("BTCUSDT"), "BTCUSDT")
        self.assertEqual(normalize_symbol(" eth/usdt "), "ETHUSDT")
        self.assertEqual(normalize_symbol("sol-usdt"), "SOLUSDT")

    def test_kosong_ditolak(self):
        for bad in ("", "   ", "---"):
            with self.assertRaises(ValueError, msg=f"symbol={bad!r}"):
                normalize_symbol(bad)


if __name__ == "__main__":
    unittest.main()
