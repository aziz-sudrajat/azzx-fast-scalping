# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Konfigurasi azzx-fast-scalping (bot scalping Binance Futures).

Kontrak:
- Konstanta modul di bawah adalah default kanonis. Boleh DITAMBAH, jangan DIKURANGI.
- Override runtime hanya lewat ``load()``: env var ``SCALP_MODE`` dan/atau file
  ``.env`` di root project. Env var selalu menang atas file.
- Modul ini TIDAK PERNAH menulis secret ke file. ``load()`` hanya membaca.

Contoh:
    from scalping import config
    cfg = config.load()          # baca SCALP_MODE / .env bila ada
    config.validate(cfg)         # fail-closed bila nilai tidak masuk akal
    base = config.get_base_url(cfg["MODE"])
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Mode & universe
# ---------------------------------------------------------------------------
MODE = "paper"  # paper | testnet | live
SYMBOLS = ["BTCUSDT"]

# ---------------------------------------------------------------------------
# Indikator
# ---------------------------------------------------------------------------
EMA_FAST = 9
EMA_SLOW = 21
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
ATR_PERIOD = 14
ATR_MIN_PERCENTILE = 20
VOLUME_SPIKE_MULT = 1.5
MAX_SPREAD_BPS = 5.0

# ---------------------------------------------------------------------------
# Exit
# ---------------------------------------------------------------------------
TP_ATR_MULT = 1.5
SL_ATR_MULT = 1.0
TIME_STOP_MIN = 15

# ---------------------------------------------------------------------------
# Scanner (short-TF fast scan)
# ---------------------------------------------------------------------------
SCANNER_MIN_VOL = 5_000_000.0
SCANNER_MIN_VOL_SPIKE = 1.5
SCANNER_ATR_MIN_PERCENTILE = 20
SCANNER_ATR_MAX_PERCENTILE = 95
SCANNER_MIN_CANDLE_RANGE_PCT = 0.02

# ---------------------------------------------------------------------------
# Risk & quality
# ---------------------------------------------------------------------------
TAKER_FEE = 0.0005
SLIPPAGE = 0.0001
MAX_DAILY_LOSS_USD = 100.0
MAX_POSITIONS = 3
COOLDOWN_SEC = 300
ATR_MIN_PERCENTILE = 20
ATR_MAX_PERCENTILE = 100   # batas atas ATR percentile; nonaktif jika 100
VOLUME_SPIKE_MULT = 1.5
MIN_CANDLE_RANGE_PCT = 0.05  # minimal range candle 1m (persen) untuk hindari chop
MAX_SPREAD_BPS = 5.0
BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_TESTNET = "https://testnet.binancefuture.com"
KLINES_LIMIT = 200
HTTP_TIMEOUT = 10.0

# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------
_VALID_MODES = ("paper", "testnet", "live")
_MODE_ENV_VAR = "SCALP_MODE"

_DEFAULT_KEYS = (
    "MODE",
    "SYMBOLS",
    "EMA_FAST",
    "EMA_SLOW",
    "MACD_FAST",
    "MACD_SLOW",
    "MACD_SIGNAL",
    "ATR_PERIOD",
    "ATR_MIN_PERCENTILE",
    "ATR_MAX_PERCENTILE",
    "SCANNER_ATR_MIN_PERCENTILE",
    "SCANNER_ATR_MAX_PERCENTILE",
    "SCANNER_MIN_VOL_SPIKE",
    "SCANNER_MIN_CANDLE_RANGE_PCT",
    "VOLUME_SPIKE_MULT",
    "MIN_CANDLE_RANGE_PCT",
    "MAX_SPREAD_BPS",
    "TP_ATR_MULT",
    "SL_ATR_MULT",
    "TIME_STOP_MIN",
    "TAKER_FEE",
    "SLIPPAGE",
    "MAX_DAILY_LOSS_USD",
    "MAX_POSITIONS",
    "COOLDOWN_SEC",
    "BINANCE_FAPI",
    "BINANCE_TESTNET",
    "KLINES_LIMIT",
    "HTTP_TIMEOUT",
)


def _defaults() -> dict[str, Any]:
    """Salinan default kanonis (list disalin agar tidak termutasi)."""
    cfg: dict[str, Any] = {}
    for key in _DEFAULT_KEYS:
        value = globals()[key]
        cfg[key] = list(value) if isinstance(value, list) else value
    return cfg


def _read_dotenv(path: Path) -> dict[str, str]:
    """Parse file .env sederhana (KEY=VALUE). Tidak pernah raise bila hilang.

    Hanya membaca; fungsi ini tidak menulis apa pun ke disk.
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[len("export "):].strip()
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def _validate_mode(raw: str) -> str:
    mode = raw.strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(
            f"SCALP_MODE tidak valid: {raw!r} "
            f"(harus salah satu dari {', '.join(_VALID_MODES)})"
        )
    return mode


def load(env_path: str | Path | None = None) -> dict[str, Any]:
    """Muat konfigurasi efektif sebagai dict.

    Urutan override (yang terakhir menang):
      1. default konstanta modul,
      2. file .env (default: ``<project_root>/.env``; bisa dioverride via argumen),
      3. env var ``SCALP_MODE``.

    Hanya ``MODE`` yang bisa dioverride dari luar; nilai selain
    paper/testnet/live ditolak dengan ValueError (fail-closed — tidak pernah
    diam-diam berjalan dengan mode tak dikenal).
    """
    cfg = _defaults()
    dotenv_path = (
        Path(env_path)
        if env_path is not None
        else Path(__file__).resolve().parent.parent / ".env"
    )
    file_values = _read_dotenv(dotenv_path)

    mode: str | None = file_values.get(_MODE_ENV_VAR)
    if _MODE_ENV_VAR in os.environ:
        mode = os.environ[_MODE_ENV_VAR]  # env var menang atas file
    if mode is not None:
        cfg["MODE"] = _validate_mode(mode)
    return cfg


def get_base_url(mode: str = MODE) -> str:
    """Pilih base URL REST sesuai mode (testnet -> testnet, sisanya -> fapi)."""
    return BINANCE_TESTNET if mode == "testnet" else BINANCE_FAPI


def validate(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validasi fail-closed atas dict konfigurasi. Raise ValueError bila janggal.

    Mengembalikan dict yang sama bila lolos, agar bisa dirangkai:
    ``cfg = validate(load())``.
    """
    mode = str(cfg.get("MODE", "")).strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(f"MODE tidak valid: {cfg.get('MODE')!r}")

    symbols = cfg.get("SYMBOLS")
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(not isinstance(s, str) or not s.strip() for s in symbols)
    ):
        raise ValueError("SYMBOLS harus list non-kosong berisi string simbol")

    def _num(key: str, lo: float | None = None, hi: float | None = None,
             strict_gt: bool = False) -> float:
        val = cfg.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(f"{key} harus angka, dapat {val!r}")
        fval = float(val)
        if lo is not None and (fval <= lo if strict_gt else fval < lo):
            op = ">" if strict_gt else ">="
            raise ValueError(f"{key} harus {op} {lo}, dapat {fval}")
        if hi is not None and fval > hi:
            raise ValueError(f"{key} harus <= {hi}, dapat {fval}")
        return fval

    ema_fast = _num("EMA_FAST", lo=1)
    ema_slow = _num("EMA_SLOW", lo=1)
    if ema_slow <= ema_fast:
        raise ValueError("EMA_SLOW harus > EMA_FAST")

    macd_fast = _num("MACD_FAST", lo=1)
    macd_slow = _num("MACD_SLOW", lo=1)
    if macd_slow <= macd_fast:
        raise ValueError("MACD_SLOW harus > MACD_FAST")
    _num("MACD_SIGNAL", lo=1)

    _num("ATR_PERIOD", lo=1)
    _num("ATR_MIN_PERCENTILE", lo=0, hi=100)
    _num("ATR_MAX_PERCENTILE", lo=0, hi=100)
    _num("SCANNER_ATR_MIN_PERCENTILE", lo=0, hi=100)
    _num("SCANNER_ATR_MAX_PERCENTILE", lo=0, hi=100)
    _num("SCANNER_MIN_VOL_SPIKE", lo=0, strict_gt=True)
    _num("SCANNER_MIN_CANDLE_RANGE_PCT", lo=0, hi=10)
    _num("VOLUME_SPIKE_MULT", lo=0, strict_gt=True)
    _num("MIN_CANDLE_RANGE_PCT", lo=0, hi=10)
    _num("MAX_SPREAD_BPS", lo=0)
    _num("TP_ATR_MULT", lo=0, strict_gt=True)
    _num("SL_ATR_MULT", lo=0, strict_gt=True)
    _num("TIME_STOP_MIN", lo=0, strict_gt=True)
    _num("TAKER_FEE", lo=0, hi=0.1)
    _num("SLIPPAGE", lo=0, hi=0.1)
    _num("MAX_DAILY_LOSS_USD", lo=0)
    _num("MAX_POSITIONS", lo=1)
    _num("COOLDOWN_SEC", lo=0)
    _num("KLINES_LIMIT", lo=1, hi=1500)
    _num("HTTP_TIMEOUT", lo=0, strict_gt=True)

    for key in ("BINANCE_FAPI", "BINANCE_TESTNET"):
        url = cfg.get(key)
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError(f"{key} harus URL https, dapat {url!r}")

    return cfg
