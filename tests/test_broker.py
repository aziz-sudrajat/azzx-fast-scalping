"""Test broker.py: PaperBroker & BinanceBroker.

Tidak ada network asli: urlopen di-mock; signature diverifikasi manual via hmac.
Jalankan dari root project:  python3 -m unittest discover -s tests -v
"""

import hashlib
import hmac
import io
import json
import unittest
import urllib.parse
from unittest.mock import patch

from scalping.broker import BinanceBroker, PaperBroker


class FakeHTTPResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestPaperBroker(unittest.TestCase):
    def test_open_close_long_pnl_benar(self):
        b = PaperBroker(1000.0)
        fill = b.market_open("BTCUSDT", "BUY", 0.5, 60000.0)
        self.assertTrue(fill["filled"])
        self.assertEqual(b.balance(), 1000.0)  # balance belum berubah sebelum close

        pos = b.get_position("BTCUSDT")
        self.assertEqual(pos["side"], "BUY")
        self.assertEqual(pos["qty"], 0.5)
        self.assertEqual(pos["avg_price"], 60000.0)

        result = b.market_close("BTCUSDT", 60500.0)
        # PnL long = (60500-60000)*0.5 = 250
        self.assertAlmostEqual(result["pnl_usd"], 250.0)
        self.assertAlmostEqual(b.balance(), 1250.0)
        self.assertIsNone(b.get_position("BTCUSDT"))

    def test_open_close_short_pnl_benar(self):
        b = PaperBroker(1000.0)
        b.market_open("ETHUSDT", "SHORT", 2.0, 3000.0)
        result = b.market_close("ETHUSDT", 2950.0)
        # PnL short = (3000-2950)*2 = 100
        self.assertAlmostEqual(result["pnl_usd"], 100.0)
        self.assertAlmostEqual(b.balance(), 1100.0)

    def test_loss_mengurangi_balance(self):
        b = PaperBroker(1000.0)
        b.market_open("SOLUSDT", "BUY", 10.0, 150.0)
        result = b.market_close("SOLUSDT", 145.0)
        self.assertAlmostEqual(result["pnl_usd"], -50.0)
        self.assertAlmostEqual(b.balance(), 950.0)

    def test_close_tanpa_posisi_error(self):
        b = PaperBroker()
        with self.assertRaises(RuntimeError):
            b.market_close("BTCUSDT", 60000.0)

    def test_buka_berlawanan_arah_ditolak(self):
        b = PaperBroker()
        b.market_open("BTCUSDT", "BUY", 0.1, 60000.0)
        with self.assertRaises(RuntimeError):
            b.market_open("BTCUSDT", "SELL", 0.1, 60000.0)

    def test_qty_price_invalid_ditolak(self):
        b = PaperBroker()
        with self.assertRaises(ValueError):
            b.market_open("BTCUSDT", "BUY", 0, 60000.0)
        with self.assertRaises(ValueError):
            b.market_open("BTCUSDT", "BUY", 0.1, -1)


class TestBinanceBrokerSign(unittest.TestCase):
    def test_sign_hmac_valid(self):
        """Verifikasi manual: signature == HMAC-SHA256(secret, query) hex."""
        broker = BinanceBroker("KEY", "SECRET", testnet=True)
        signed = broker._sign({"symbol": "BTCUSDT", "side": "BUY"})
        self.assertIn("signature", signed)
        self.assertIn("timestamp", signed)
        self.assertEqual(signed["recvWindow"], 5000)

        query = urllib.parse.urlencode(
            {k: v for k, v in signed.items() if k != "signature"}
        )
        expected = hmac.new(b"SECRET", query.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(signed["signature"], expected)

    def test_base_url_testnet_dan_live(self):
        self.assertEqual(
            BinanceBroker("k", "s", testnet=True).base_url,
            "https://testnet.binancefuture.com",
        )
        self.assertEqual(
            BinanceBroker("k", "s", testnet=False).base_url,
            "https://fapi.binance.com",
        )

    def test_kredensial_kosong_ditolak(self):
        with self.assertRaises(ValueError):
            BinanceBroker("", "s")


class TestBinanceBrokerNetworkMock(unittest.TestCase):
    def _patch(self, payload: dict):
        body = json.dumps(payload).encode()
        return patch("urllib.request.urlopen", return_value=FakeHTTPResponse(body))

    def test_place_market_sukses_mock(self):
        broker = BinanceBroker("KEY", "SECRET", testnet=True)
        fake = {"orderId": 12345, "symbol": "BTCUSDT", "status": "FILLED"}
        with self._patch(fake):
            result = broker.place_market("BTCUSDT", "BUY", 0.01)
        self.assertEqual(result["orderId"], 12345)
        self.assertEqual(result["status"], "FILLED")

    def test_place_market_side_sell_quantity_invalid(self):
        broker = BinanceBroker("KEY", "SECRET", testnet=True)
        with self.assertRaises(ValueError):
            broker.place_market("BTCUSDT", "BUY", 0)

    def test_place_market_respons_tak_terduga_error(self):
        broker = BinanceBroker("KEY", "SECRET", testnet=True)
        with self._patch({"msg": "no order id"}):
            with self.assertRaises(RuntimeError):
                broker.place_market("BTCUSDT", "BUY", 0.01)

    def test_get_position_ada(self):
        broker = BinanceBroker("KEY", "SECRET", testnet=True)
        fake = [
            {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "60000.0"},
            {"symbol": "ETHUSDT", "positionAmt": "0", "entryPrice": "0"},
        ]
        with self._patch(fake):
            pos = broker.get_position("BTCUSDT")
        self.assertEqual(pos["amt"], 0.5)
        self.assertEqual(pos["entry_price"], 60000.0)
        self.assertEqual(pos["side"], "BUY")

    def test_get_position_flat_none(self):
        broker = BinanceBroker("KEY", "SECRET", testnet=True)
        fake = [{"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "0"}]
        with self._patch(fake):
            self.assertIsNone(broker.get_position("BTCUSDT"))
            self.assertIsNone(broker.get_position("DOGEUSDT"))


if __name__ == "__main__":
    unittest.main()
