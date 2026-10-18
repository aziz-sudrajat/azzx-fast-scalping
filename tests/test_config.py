# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Test config.py: default kontrak + override env/.env + validasi."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scalping import config


class TestDefaults(unittest.TestCase):
    def test_kontrak_konstanta(self):
        self.assertEqual(config.MODE, "paper")
        self.assertEqual(config.SYMBOLS, ["BTCUSDT"])
        self.assertEqual(config.EMA_FAST, 9)
        self.assertEqual(config.EMA_SLOW, 21)
        self.assertEqual(config.MACD_FAST, 12)
        self.assertEqual(config.MACD_SLOW, 26)
        self.assertEqual(config.MACD_SIGNAL, 9)
        self.assertEqual(config.ATR_PERIOD, 14)
        self.assertEqual(config.ATR_MIN_PERCENTILE, 20)
        self.assertEqual(config.VOLUME_SPIKE_MULT, 1.5)
        self.assertEqual(config.MAX_SPREAD_BPS, 5.0)
        self.assertEqual(config.TP_ATR_MULT, 1.5)
        self.assertEqual(config.SL_ATR_MULT, 1.0)
        self.assertEqual(config.TIME_STOP_MIN, 15)
        self.assertEqual(config.TAKER_FEE, 0.0005)
        self.assertEqual(config.SLIPPAGE, 0.0001)
        self.assertEqual(config.MAX_DAILY_LOSS_USD, 100.0)
        self.assertEqual(config.MAX_POSITIONS, 3)
        self.assertEqual(config.COOLDOWN_SEC, 300)
        self.assertEqual(config.BINANCE_FAPI, "https://fapi.binance.com")
        self.assertEqual(
            config.BINANCE_TESTNET, "https://testnet.binancefuture.com"
        )
        self.assertEqual(config.KLINES_LIMIT, 200)
        self.assertEqual(config.HTTP_TIMEOUT, 10.0)

    def test_load_default_tanpa_override(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = config.load(env_path="/tmp/tidak-ada-xyz/.env")
        self.assertEqual(cfg["MODE"], "paper")
        self.assertEqual(cfg["SYMBOLS"], ["BTCUSDT"])

    def test_load_tidak_memutasi_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = config.load(env_path="/tmp/tidak-ada-xyz/.env")
        cfg["SYMBOLS"].append("ETHUSDT")
        self.assertEqual(config.SYMBOLS, ["BTCUSDT"])


class TestEnvOverride(unittest.TestCase):
    def test_scalp_mode_env(self):
        with mock.patch.dict(os.environ, {"SCALP_MODE": "testnet"}):
            self.assertEqual(config.load()["MODE"], "testnet")

    def test_scalp_mode_case_insensitive(self):
        with mock.patch.dict(os.environ, {"SCALP_MODE": "LIVE"}):
            self.assertEqual(config.load()["MODE"], "live")

    def test_scalp_mode_invalid_fail_closed(self):
        with mock.patch.dict(os.environ, {"SCALP_MODE": "bogus"}):
            with self.assertRaises(ValueError):
                config.load()


class TestDotenv(unittest.TestCase):
    def _write_env(self, directory: str, content: str) -> Path:
        path = Path(directory) / ".env"
        path.write_text(content, encoding="utf-8")
        return path

    def test_dotenv_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = self._write_env(tmp, "SCALP_MODE=testnet\n")
            with mock.patch.dict(os.environ, {}, clear=True):
                cfg = config.load(env_path=env_path)
            self.assertEqual(cfg["MODE"], "testnet")

    def test_dotenv_komentar_dan_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = self._write_env(
                tmp, '# komentar\nSCALP_MODE="live"\nFOO=bar\n'
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                cfg = config.load(env_path=env_path)
            self.assertEqual(cfg["MODE"], "live")

    def test_env_menang_atas_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = self._write_env(tmp, "SCALP_MODE=paper\n")
            with mock.patch.dict(os.environ, {"SCALP_MODE": "live"}):
                cfg = config.load(env_path=env_path)
            self.assertEqual(cfg["MODE"], "live")

    def test_load_tidak_pernah_menulis_file(self):
        # load() hanya membaca: direktori tmp harus tetap kosong
        with tempfile.TemporaryDirectory() as tmp:
            before = set(os.listdir(tmp))
            with mock.patch.dict(os.environ, {"SCALP_MODE": "paper"}):
                config.load(env_path=Path(tmp) / ".env")  # file tidak ada
            self.assertEqual(set(os.listdir(tmp)), before)


class TestHelpers(unittest.TestCase):
    def test_get_base_url(self):
        self.assertEqual(config.get_base_url("paper"), config.BINANCE_FAPI)
        self.assertEqual(config.get_base_url("live"), config.BINANCE_FAPI)
        self.assertEqual(config.get_base_url("testnet"), config.BINANCE_TESTNET)

    def test_validate_lolos(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = config.load(env_path="/tmp/tidak-ada-xyz/.env")
        self.assertIs(config.validate(cfg), cfg)

    def test_validate_menolak_mode_aneh(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = config.load(env_path="/tmp/tidak-ada-xyz/.env")
        cfg["MODE"] = "yolo"
        with self.assertRaises(ValueError):
            config.validate(cfg)

    def test_validate_menolak_nilai_risiko_aneh(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = config.load(env_path="/tmp/tidak-ada-xyz/.env")
        for key, bad in (
            ("EMA_SLOW", 9),        # harus > EMA_FAST
            ("TP_ATR_MULT", -1.0),  # harus > 0
            ("MAX_POSITIONS", 0),   # harus >= 1
            ("TAKER_FEE", 0.5),     # di atas 10% tak masuk akal
            ("SYMBOLS", []),        # tak boleh kosong
        ):
            bad_cfg = dict(cfg)
            bad_cfg[key] = bad
            with self.assertRaises(ValueError, msg=f"{key}={bad!r}"):
                config.validate(bad_cfg)


if __name__ == "__main__":
    unittest.main()
