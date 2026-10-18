# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Screener coin terbaik untuk scalping **cepat (short-timeframe)**.

Jalankan langsung:  python scan.py

Arsitektur lama (fungsi ``scan``): cuma klines 15m, momentum terbatas dari
jendela klines saja, analisis berurutan. Rekomendasi: gunakan ``scan_fast()``
yang membaca **short-TF 1m/5m/15m** sekaligus:

  - momentum  1m / 5m / 15m (dan 1m rate-of-change)
  - ATR 1m/5m/15m (nilai absolut + pct) & persentil band
  - volume spike 1m (bar terakhir vs 20-bar avg)
  - candle range 1m (filter chop / fatigue)
  - kualitas sinyal (kombinasi ATR band + range + momentum minimal)
  - TP / SL / time-stop otomatis dari ATR 1m (bisa di-feed ke trader)

Kecepatan: kandidat dianalisis pararel lewat ThreadPoolExecutor; tiap coin
mencoba Binance dulu, fallback OKX kalau Binance terkunci; data candle lebih
pekat (1m/5m) sehingga detektor momentum "melihat" yang sedang terjadi, bukan
yang terjadi 10 jam lalu.

Rutinitas:
    python scan.py                      # fast short-TF (rekomendasi)
    python scan.py --legacy             # scan 15m lama (compat)
"""

from __future__ import annotations

import csv
import json
import math
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from .indicators import atr, atr_percentile, true_range
from .live_loop import fmt_price, paint

# ------------------------------------------------------------------ URLS
BASE_URL_BINANCE = "https://fapi.binance.com"
BASE_URL_OKX = "https://www.okx.com"

# Symbol exclusions (shared)
_EXCLUDE_QUOTES = ("USDC", "FDUSD", "TUSD", "DAI", "EUR", "BRL")
_EXCLUDE_SUFFIX = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
_EXCLUDE_BASE = ("USDD", "USDJ", "XAUT", "PAXG", "BFUSD")

# Tunable scan-fast defaults
SCAN_MIN_VOL = 5_000_000.0
SCAN_MIN_VOL_SPIKE = 1.5
SCAN_ATR_MIN_PERCENTILE = 20.0
SCAN_ATR_MAX_PERCENTILE = 95.0
SCAN_MIN_CANDLE_RANGE_PCT = 0.02
SCAN_LIMIT_1M = 40
SCAN_LIMIT_5M = 140
SCAN_LIMIT_15M = 35
SCAN_MAX_WORKERS = 8

# Maksimal usia candle (detik) supaya tidak pusing datanya
_FRESH_MAX_AGE = {"1m": 90.0, "5m": 900.0, "15m": 1800.0}

# Warna grade
_GRADE_COLOR = {"A": "green", "B": "green", "C": "yellow",
                "D": "yellow", "F": "dim"}


# ------------------------------------------------------------------ helpers
_MAX_ATTEMPTS = 3


def _http_get(url: str, timeout: int = 15) -> Any:
    """GET + JSON parse. Retry transient errors (429/502/503, socket timeout)
    dengan backoff. Fail-closed: raise bila tetap gagal."""
    for attempt in range(_MAX_ATTEMPTS):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "azzx-scan/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503) and attempt < _MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt * 2.0)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception as e:
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def _last(vals: list) -> Any:
    """Nilai valid terakhir (lewati None); None bila tak ada."""
    for v in reversed(vals):
        if v is not None:
            return v
    return None


def _bool_ok(val: Any) -> bool:
    return val is True or (isinstance(val, (int, float)) and bool(val))


def _fresh(candles: list[dict], interval: str) -> bool:
    """Candle tertutup 'masih segar' (bukan candle yang masih berjalan dan
    usainya <= batas interval). Fail-closed: False kalau datanya tidak dipercaya.
    """
    if not candles:
        return False
    last = candles[-1]
    ct = last.get("close_time") or last.get("open_time")
    if ct is None:
        return False
    try:
        age = time.time() - float(ct) / 1000.0
    except (TypeError, ValueError):
        return False
    if age < 0:
        return False
    return age <= _FRESH_MAX_AGE.get(interval, 900.0)


def _klines(symbol: str, interval: str, limit: int) -> Optional[list[dict]]:
    """Fetch klines: Binance dulu, OKX fallback. Fail-closed."""
    # Binance
    try:
        raw = _http_get(
            f"{BASE_URL_BINANCE}/fapi/v1/klines?symbol={symbol}"
            f"&interval={interval}&limit={limit}")
        if not _fresh(raw, interval):
            raise RuntimeError("candle Binance usang/masih berjalan")
        out = []
        for row in raw:
            out.append({"open_time": row[0], "open": float(row[1]),
                        "high": float(row[2]), "low": float(row[3]),
                        "close": float(row[4]), "volume": float(row[5]),
                        "close_time": row[6]})
        return out[:-1]  # buang candle yang masih berjalan
    except Exception:
        pass
    # OKX fallback
    try:
        inst = _inst_id_okx(symbol)
        dur = {"1m": 60000, "5m": 300000, "15m": 900000}[interval]
        raw = _http_get(
            f"{BASE_URL_OKX}/api/v5/market/candles"
            f"?instId={inst}&bar={interval}&limit={limit}")
        rows = raw.get("data", []) if isinstance(raw, dict) else []
        if not rows:
            raise RuntimeError(f"tidak ada candle OKX untuk {symbol}")
        out = []
        for r in rows:
            out.append({"open_time": int(r[0]), "open": float(r[1]),
                        "high": float(r[2]), "low": float(r[3]),
                        "close": float(r[4]), "volume": float(r[5]),
                        "close_time": int(r[0]) + dur})
        return out[:-1]
    except Exception as e:
        raise RuntimeError(f"klines gagal untuk {symbol} ({interval}): {e}") from e


def _inst_id_okx(symbol: str) -> str:
    """BTCUSDT -> 'BTC-USDT-SWAP' (OKX)."""
    s = symbol.upper().replace("-", "").replace("_", "")
    if s.endswith("USDT"):
        return f"{s[:-4]}-USDT-SWAP"
    return f"{s}-USDT-SWAP"


def _norm_okx_symbol(symbol: str) -> str:
    """OKX 'BTC-USDT-SWAP' -> 'BTCUSDT'."""
    s = symbol.upper().replace("-", "").replace("_", "")
    if s.endswith("-SWAP"):
        s = s[:-5]
    elif s.endswith("SWAP"):
        s = s[:-4]
    return s


def _bell(x: float, center: float, half_width: float,
          floor: float = 0.0, cap: float = 100.0) -> float:
    """Bell (triangular) 0..cap yang peak di `center` dan turun ke `floor`
    saat `|x - center| >= half_width`."""
    if not math.isfinite(x):
        return floor
    d = abs(x - center)
    if d >= half_width:
        return floor
    return floor + (cap - floor) * (1.0 - d / half_width)


# ------------------------------------------------------------------ fetch
def fetch_tickers() -> list[dict]:
    """Semua ticker futures (Binance). Fail-closed: error -> SystemExit."""
    try:
        data = _http_get(f"{BASE_URL_BINANCE}/fapi/v1/ticker/24hr")
    except Exception as e:
        raise SystemExit(f"scan gagal ambil ticker Binance: {e}")
    if not isinstance(data, list):
        raise SystemExit("respons ticker Binance tak dikenal")
    return data


def fetch_tickers_okx() -> list[dict]:
    """Semua ticker USDT perpetual dari OKX. Fail-closed."""
    try:
        data = _http_get(f"{BASE_URL_OKX}/api/v5/market/tickers?instType=SWAP",
                         timeout=15)
    except Exception as e:
        raise SystemExit(f"scan gagal ambil ticker OKX: {e}")
    if not isinstance(data, dict):
        raise SystemExit("respons ticker OKX tak dikenal")
    rows = data.get("data", [])
    if not rows:
        raise SystemExit("tidak ada data ticker OKX")
    out = []
    for t in rows:
        sym = _norm_okx_symbol(t.get("instId", ""))
        if not sym.endswith("USDT"):
            continue
        if sym.endswith(_EXCLUDE_SUFFIX):
            continue
        base = sym[:-4]
        if base in _EXCLUDE_QUOTES or base in _EXCLUDE_BASE:
            continue
        try:
            last = float(t.get("last", "0"))
            open24h = float(t.get("open24h", "0"))
            chg = (last - open24h) / open24h * 100.0 if open24h > 0 else 0.0
            qv = float(t.get("vol24h", "0")) * last
        except (TypeError, ValueError):
            continue
        if qv <= 0 or last <= 0:
            continue
        out.append({"symbol": sym, "quote_vol": qv, "change_pct": chg,
                    "price": last})
    out.sort(key=lambda r: r["quote_vol"], reverse=True)
    return out


# ------------------------------------------------------------------ candidat
def pick_candidates(tickers: list[dict], min_quote_vol: float,
                    limit: int) -> list[dict]:
    """Filter USDT perp yang layak: volume cukup, bukan stable/leveraged."""
    out = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        if sym.endswith(_EXCLUDE_SUFFIX):
            continue
        base = sym[:-4]
        if base in _EXCLUDE_QUOTES or base in _EXCLUDE_BASE:
            continue
        try:
            qv = float(t.get("quoteVolume", t.get("quote_vol", "0")))
            pc = float(t.get("priceChangePercent", t.get("change_pct", "0")))
            last = float(t.get("lastPrice", t.get("price", "0")))
        except (TypeError, ValueError):
            continue
        if qv < min_quote_vol or last <= 0:
            continue
        out.append({"symbol": sym, "quote_vol": qv,
                    "change_pct": pc, "price": last})
    out.sort(key=lambda r: r["quote_vol"], reverse=True)
    return out[:limit]


# ------------------------------------------------------------------ analisis short-TF
def analyze_fast(symbol: str,
                 limit_1m: int = SCAN_LIMIT_1M,
                 limit_5m: int = SCAN_LIMIT_5M,
                 limit_15m: int = SCAN_LIMIT_15M) -> Optional[dict]:
    """Hitung metrik scalping short-TF untuk satu symbol. None bila data kurang.

    Metrik yang dihasilkan (kunci dict):
      atr_1m | atr_5m | atr_15m      : nilai ATR absolut
      atr_pct_1m | atr_pct_5m | atr_pct_15m : ATR/prepin (persen)
      atr_pct_rank   : persentil ATR 1m vs historis
      mom_1m | mom_5m | mom_15m     : % perubahan bar terkini (1m/5m/15m)
      roc_1m         : % rate-of-change 1m
      vol_spike_1m   : volume bar terakhir / 20-bar avg
      vol_trend_5m | vol_trend_1m  : x (>1 = naik)
      candle_range_1m: rata-rata true range 1m / harga (%),
                       gate chop/fatigue
      price          : harga close terakhir
    """
    try:
        kl1 = _klines(symbol, "1m", limit_1m)
        kl5 = _klines(symbol, "5m", limit_5m)
        kl15 = _klines(symbol, "15m", limit_15m)
    except RuntimeError as e:
        print(f"  ⏳ {symbol}: {e}", flush=True)
        return None
    if kl1 is None or len(kl1) < 30:
        return None
    if kl5 is None or len(kl5) < 10:
        return None
    if kl15 is None or len(kl15) < 22:
        return None

    close1 = [float(c["close"]) for c in kl1]
    if close1[-1] <= 0:
        return None

    # --- ATR ---
    atr1 = atr(kl1, 14)
    atr1_now = _last(atr1)
    if atr1_now is None or atr1_now <= 0:
        return None
    atr1_pct = atr1_now / close1[-1] * 100.0

    atr5 = atr(kl5, 14)
    atr5_now = _last(atr5)
    atr5_pct = (atr5_now / close1[-1] * 100.0) if (atr5_now and atr5_now > 0) else None

    atr15 = atr(kl15, 14)
    atr15_now = _last(atr15)
    atr15_pct = (atr15_now / close1[-1] * 100.0) if (atr15_now and atr15_now > 0) else None

    # --- momentum ---
    mom_1m = (close1[-1] / close1[-2] - 1.0) * 100.0 if len(close1) >= 2 else 0.0
    mom_5m = (close1[-1] / close1[-6] - 1.0) * 100.0 if len(close1) >= 6 else 0.0
    mom_15m = (close1[-1] / close1[-16] - 1.0) * 100.0 if len(close1) >= 16 else 0.0

    # --- volume ---
    vols1 = [float(c.get("volume", 0.0) or 0.0) for c in kl1]
    avg20 = sum(vols1[-22:-2]) / 20.0 if len(vols1) >= 22 else (sum(vols1) / len(vols1) if vols1 else 0.0)
    vol_spike = vols1[-1] / avg20 if avg20 > 0 else 1.0
    avg10 = sum(vols1[-10:]) / 10.0
    prev10 = sum(vols1[-20:-10]) / 10.0 if len(vols1) >= 20 else avg10
    vol_trend_1m = avg10 / prev10 if prev10 > 0 else 1.0

    # --- volume trend 5m ---
    vols5 = [float(c.get("volume", 0.0) or 0.0) for c in kl5]
    avg5_10 = sum(vols5[-10:]) / 10.0 if len(vols5) >= 10 else 1.0
    avg5_prev = (sum(vols5[-20:-10]) / 10.0) if len(vols5) >= 20 else avg5_10
    vol_trend_5m = avg5_10 / avg5_prev if avg5_prev > 0 else 1.0

    # --- candle range (chop filter) ---
    tr1 = true_range(kl1)
    tr10 = tr1[-10:]
    candle_range_1m = (sum(tr10) / 10.0) / close1[-1] * 100.0 if tr10 else None

    # --- ATR percentile band ---
    atr_pct_rank = atr_percentile(atr1, 100)

    return {
        "atr_1m": atr1_now,
        "atr_5m": atr5_now,
        "atr_15m": atr15_now,
        "atr_pct_1m": atr1_pct,
        "atr_pct_5m": atr5_pct,
        "atr_pct_15m": atr15_pct,
        "atr_pct_rank": atr_pct_rank,
        "mom_1m": mom_1m,
        "mom_5m": mom_5m,
        "mom_15m": mom_15m,
        "roc_1m": mom_1m,
        "vol_spike_1m": vol_spike,
        "vol_trend_5m": 1.0,
        "vol_trend_1m": vol_trend_1m,
        "candle_range_1m": candle_range_1m,
        "price": close1[-1],
    }


# ------------------------------------------------------------------ scoring
def _score_liquidity(qv: float) -> float:
    """Volume -> skor 0..100 (log). $5M ~ 40, $200M ~ 68, $1B ~ 85, $10B ~ 93."""
    v = max(float(qv), 1.0)
    return min(100.0, max(0.0, 20.0 + 25.0 * math.log10(v / 1e6 + 1.0)))


def _score_mom5(m: dict) -> float:
    """Momentum 5m: peak 100 saat |mom_5m| = 2%, turun ke 0 saat 0 atau >= 5%."""
    a = abs(float(m.get("mom_5m", 0.0)))
    if a <= 0.2:
        return a / 0.2 * 20.0
    if a <= 2.0:
        return 20.0 + (a - 0.2) / 1.8 * 80.0
    if a <= 5.0:
        return 100.0 - (a - 2.0) / 3.0 * 100.0
    return 0.0


def _score_spike(x: float) -> float:
    """Volume spike 1m: 0 saat < 0.5x, 100 saat >= 3x."""
    if x <= 0.5:
        return 0.0
    if x >= 3.0:
        return 100.0
    return (x - 0.5) / 2.5 * 100.0


def _score_atr(a: float) -> float:
    """ATR 1m: peak 100 saat 0.35-0.7%, 0 saat < 0.05% atau > 1.5%."""
    if a < 0.05:
        return 0.0
    if a < 0.2:
        return a / 0.2 * 100.0
    if a <= 0.7:
        return 100.0
    if a < 1.5:
        return 100.0 - (a - 0.7) / 0.8 * 100.0
    return 0.0


def _score_trend(x: float) -> float:
    """Volume trend 5m: bell peak di 1.05x, 0 saat <= 0.4x atau >= 1.7x."""
    x = float(x)
    if x <= 0.4 or x >= 1.7:
        return 0.0
    return (1.0 - abs(x - 1.05) / 0.3) * 100.0


def _quality(m: dict) -> float:
    """Skor kualitas 0..100: ATR band (absolut) + range candle + momentum."""
    s = 0.0
    a = m.get("atr_pct_1m")
    if a is not None:
        if 0.10 <= a <= 1.5:
            s += 50.0
        else:
            s += max(0.0, 100.0 - abs(a - 0.8) / 0.7 * 50.0)
    rng = m.get("candle_range_1m")
    if rng is not None:
        if 0.02 <= rng <= 1.5:
            s += 30.0
        else:
            s += max(0.0, 30.0 - abs(rng - 0.61) / 0.59 * 30.0)
    if abs(float(m.get("mom_5m", 0.0))) >= 0.3:
        s += 20.0
    return min(100.0, s)


def score_fast(cand: dict, m: dict) -> float:
    """Skor 0..100 untuk short-TF. Bobot: momentum 30, volatilitas 20,
    kualitas 15, volume spike 15, likuiditas 10, trend 10."""
    w = {"liquidity": 0.10, "mom5": 0.30, "spike": 0.15,
         "atr": 0.20, "quality": 0.15, "trend": 0.10}
    s_vol = _score_liquidity(cand.get("quote_vol", 0.0))
    s_mom = _score_mom5(m)
    s_spike = _score_spike(m.get("vol_spike_1m", 0.0))
    s_atr = _score_atr(m.get("atr_pct_1m", 0.0))
    s_qual = _quality(m)
    s_trend = _score_trend(m.get("vol_trend_5m", 1.0))
    return (w["liquidity"] * s_vol + w["mom5"] * s_mom + w["spike"] * s_spike
            + w["atr"] * s_atr + w["quality"] * s_qual + w["trend"] * s_trend)


def qualify_fast(m: dict) -> tuple[bool, list[str]]:
    """Gerbang kualitas untuk scanner short-TF (hard gates):
    - ATR 1m dalam band absolut (0.10% - 1.5%) [dead / over-extension]
    - momentum 5m minimal 0.3%
    - volume spike 1m >= 1.5x
    - range candle 1m dalam [0.02%, 1.5%] (chop / fatigue)
    - volume 5m naik.
    Return (boleh, alasan).
    """
    reasons: list[str] = []
    a = m.get("atr_pct_1m")
    if a is None:
        reasons.append("ATR1m belum tersedia (data kurang)")
    elif a < 0.01:
        reasons.append("ATR1m terlalu kecil (dead market)")
    elif a > 1.5:
        reasons.append("ATR1m terlalu besar (over-extension)")
    if abs(float(m.get("mom_5m", 0.0))) < 0.3:
        reasons.append("momentum 5m lemah (< 0.3%/5m)")
    if float(m.get("vol_spike_1m", 0.0)) < 1.5:
        reasons.append("volume 1m belum naik 1.5x")
    rng = m.get("candle_range_1m")
    if rng is not None:
        if rng < 0.02:
            reasons.append("range 1m terlalu kecil (chop)")
        elif rng > 1.5:
            reasons.append("range 1m terlalu lebar (fatigue/rage)")
    if float(m.get("vol_trend_5m", 1.0)) < 0.9:
        reasons.append("volume 5m menurun (belum ada minat pasar)")
    return (len(reasons) == 0), reasons


def _grade_rank(g: str) -> int:
    """A..F -> 4..0 (A=4 paling tinggi, F=0 paling rendah; untuk filter
    `grade >= min_grade` dengan nilai >=, dan sorting asc = A duluan)."""
    return {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}[str(g).upper()]


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 55:
        return "D"
    return "F"


def recommend(cand: dict, m: dict) -> Optional[dict]:
    """Buat rekomendasi entry/TP/SL/time-stop dari ATR 1m, khusus setup
    A/B (kuat, short-TF). Return dict atau None kalau gerbang gagal."""
    score = score_fast(cand, m)
    m2 = dict(m)
    m2.update(cand)
    m2["score"] = score
    m2["grade"] = _grade(score)
    ok, _ = qualify_fast(m2)
    if not ok or m2.get("grade") not in ("A", "B"):
        return None
    price = float(m2["price"])
    atr1 = float(m2.get("atr_1m", 0.0))
    if atr1 <= 0:
        return None
    tp_mult = 1.5
    sl_mult = 1.0
    ts = int(15)
    side = "long" if float(m2.get("mom_5m", 0.0)) >= 0 else "short"
    if side == "long":
        tp = price + tp_mult * atr1
        sl = price - sl_mult * atr1
    else:
        tp = price - tp_mult * atr1
        sl = price + sl_mult * atr1
    reason = (f"{'bullish' if side == 'long' else 'bearish'} 15m; "
              f"mom 5m {m2.get('mom_5m', 0):+.2f}%/5m; "
              f"spike {m2.get('vol_spike_1m', 0):.1f}x")
    return {"symbol": cand["symbol"], "side": side, "entry": price,
            "atr_1m": atr1, "tp": tp, "sl": sl,
            "time_stop_min": ts, "grade": m2["grade"], "score": score,
            "reason": reason}


# ------------------------------------------------------------------ scan
def _analyze_one(cand: dict) -> Optional[dict]:
    try:
        m = analyze_fast(cand["symbol"])
        if m is None:
            return None
        s = score_fast(cand, m)
        m2 = dict(m)
        m2.update(cand)
        m2["score"] = s
        m2["grade"] = _grade(s)
        ok, reasons = qualify_fast(m2)
        m2["quality_ok"] = ok
        m2["quality_reasons"] = reasons or []
        # TP/SL/entry hanya untuk setup grade A/B (kuat short-TF)
        rec = recommend(cand, m)
        if rec is not None:
            m2.update({"entry": rec["entry"], "tp": rec["tp"],
                       "sl": rec["sl"], "time_stop_min": rec["time_stop_min"],
                       "side": rec["side"]})
        return m2
    except Exception:
        return None


def _scan_batch(cands: list[dict], parallel: bool) -> list[dict]:
    tasks = list(cands)
    results: list[dict] = []
    if parallel and len(tasks) > 1:
        workers = min(SCAN_MAX_WORKERS, len(tasks))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_analyze_one, c) for c in tasks]
            for fut in as_completed(futs):
                r = fut.result()
                if r is not None:
                    results.append(r)
    else:
        for c in tasks:
            r = _analyze_one(c)
            if r is not None:
                results.append(r)
    return results


def scan_fast(top_n: int = 20, min_vol: float = SCAN_MIN_VOL,
              limit: int = 50, parallel: bool = True,
              min_grade: str = "C", quiet: bool = False,
              symbols: Optional[list[str]] = None) -> list[dict]:
    """Scan penuh short-TF. Sumber: Binance (fallback OKX) jika `symbols`
    belum diberikan. Kembalikan list ranking (skor desc), hanya grade >= min_grade.
    """
    if symbols:
        cands = [{"symbol": s, "quote_vol": 0.0, "change_pct": 0.0,
                  "price": 0.0} for s in symbols]
    else:
        try:
            tickers = fetch_tickers()
        except SystemExit as e:
            print(f"  ⬇️ {e}", flush=True)
            try:
                tickers = fetch_tickers_okx()
                if not quiet:
                    print("  🔄 menggunakan OKX data", flush=True)
            except SystemExit as e2:
                raise SystemExit(f"scan gagal: Binance & OKX keduanya gagal: {e2}")
        cands = pick_candidates(tickers, min_vol, limit)
    if not quiet:
        print(f"menganalisis {len(cands)} coin teratas by volume...",
              flush=True)
    # spread_bps: ditaruh di sini supaya nanti bisa dipakai tracker/signal
    # (tidak dihitung di dalam scanner, karena publik API tidak membawanya).
    results = _scan_batch(cands, parallel)
    results.sort(key=lambda r: r["score"], reverse=True)
    min_rank = _grade_rank(min_grade.upper())
    results = [r for r in results if _grade_rank(r["grade"]) >= min_rank]
    return results[:top_n]


# ------------------------------------------------------------------ output
def print_table_fast(results: list[dict], quiet: bool = False) -> None:
    if not results:
        print("(tidak ada koin memenuhi syarat)")
        return
    print()
    print(paint(f"{'#':<3} {'SYMBOL':<13} {'HARGA':<11} {'24H%':>7}  "
                f"{'VOL 24H':>9}  {'ATR1M%':>6}  {'MOM5M%':>7}  "
                f"{'SPIKE':>6}  {'RANGE':>6}  {'SKOR':>5}  {'GR':>3}   TP / SL",
                "bold", "cyan"))
    print(paint("-" * 130, "dim"))
    for i, r in enumerate(results, 1):
        chg = r.get("change_pct", 0.0)
        chg_s = f"{chg:+.1f}%"
        chg_c = "green" if chg >= 0 else "red"
        score_c = "green" if r["score"] >= 80 else ("yellow" if r["score"] >= 60 else "dim")
        grade_c = _GRADE_COLOR.get(r.get("grade", "F"), "dim")
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}")
        tp = fmt_price(r.get("tp", 0.0)) if r.get("tp") else "—"
        sl = fmt_price(r.get("sl", 0.0)) if r.get("sl") else "—"
        entry = fmt_price(r.get("entry", r.get("price", 0.0))) if r.get("entry") else fmt_price(r.get("price", 0.0))
        plain = (f"{medal:<3} {r['symbol']:<13} {fmt_price(r['price']):<11} "
                 f"{chg_s:>7}  {_fmt_vol(r['quote_vol']):>9}  "
                 f"{r.get('atr_pct_1m', 0):>6.2f}%  {r.get('mom_5m', 0):>7.2f}%  "
                 f"{r.get('vol_spike_1m', 0):>6.2f}x  "
                 f"{r.get('candle_range_1m', 0):>6.3f}%  "
                 f"{r['score']:>5.0f}  {r.get('grade','?'):>3}   {tp}/{sl}@{entry}")
        print(paint(plain, score_c, grade_c))
    print()
    if results:
        best = next((r for r in results if r.get("grade") in ("A", "B")),
                    results[0])
        print(paint(f"⭐ Rekomendasi: {best['symbol']} (skor {best['score']:.0f}, "
                    f"GR {best.get('grade','?')}), arah {best.get('side','?')}: "
                    f"entry {fmt_price(best.get('entry', best.get('price', 0)))} | "
                    f"TP {fmt_price(best.get('tp', 0))} | SL {fmt_price(best.get('sl', 0))} | "
                    f"time-stop {best.get('time_stop_min', 15)} menit", "green", "bold"))
        if best.get("reason"):
            print(paint(f"   alasan: {best['reason']}", "dim"))
        if best.get("grade", "F") not in ("A", "B"):
            print(paint(f"   (catatan: top-1 belum A/B; lihat daftar untuk setup A/B)",
                        "yellow"))
    if not quiet:
        print(paint(f"selesai", "dim"))


def _fmt_vol(qv: float) -> str:
    if qv >= 1e9:
        return f"${qv / 1e9:.1f}B"
    if qv >= 1e6:
        return f"${qv / 1e6:.0f}M"
    return f"${qv / 1e3:.0f}K"


def to_csv(results: list[dict], path: str) -> None:
    """Export hasil scan ke CSV."""
    keys = ["symbol", "price", "quote_vol", "change_pct", "atr_pct_1m",
            "mom_1m", "mom_5m", "mom_15m", "roc_1m", "vol_spike_1m",
            "vol_trend_5m", "candle_range_1m", "score", "grade",
            "tp", "sl", "time_stop_min", "side", "reason"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in results:
            row = {k: r.get(k, "") for k in keys}
            w.writerow(row)


# ------------------------------------------------------------------ legacy
def analyze(symbol: str) -> dict | None:
    """Hitung metrik scalping 15m (legacy). None bila data kurang."""
    try:
        kl = _klines_impl_legacy(symbol)
    except Exception:
        return None
    if len(kl) < 30:
        return None
    closes = [k["close"] for k in kl]
    vols = [k["volume"] for k in kl]
    atr_vals = atr(kl, 14)
    atr_now = _last(atr_vals)
    if atr_now is None or closes[-1] <= 0:
        return None
    atr_pct = atr_now / closes[-1] * 100
    momentum = (closes[-1] - closes[0]) / closes[0] * 100
    recent_vol = sum(vols[-10:]) / 10
    prev_vol = sum(vols[-20:-10]) / 10 if len(vols) >= 20 else recent_vol
    vol_trend = (recent_vol / prev_vol) if prev_vol > 0 else 1.0
    return {"atr_pct": atr_pct, "momentum": momentum,
            "vol_trend": vol_trend, "price": closes[-1]}


def _klines_impl_legacy(symbol: str, limit: int = 100) -> list[dict]:
    """Internal klines fetcher: Binance dulu, OKX fallback (legacy scan)."""
    try:
        url = (f"{BASE_URL_BINANCE}/fapi/v1/klines?symbol={symbol}"
               f"&interval=15m&limit={limit}")
        raw = _http_get(url)
        out = []
        for row in raw:
            out.append({"open_time": row[0], "open": float(row[1]),
                        "high": float(row[2]), "low": float(row[3]),
                        "close": float(row[4]), "volume": float(row[5]),
                        "close_time": row[6]})
        return out[:-1]
    except Exception:
        pass
    try:
        inst = _inst_id_okx(symbol)
        url = (f"{BASE_URL_OKX}/api/v5/market/candles"
               f"?instId={inst}&bar=15m&limit={limit}")
        raw = _http_get(url)
        rows = raw.get("data", []) if isinstance(raw, dict) else []
        if not rows:
            raise RuntimeError(f"tidak ada candle OKX untuk {symbol}")
        out = []
        for r in rows:
            out.append({"open_time": int(r[0]), "open": float(r[1]),
                        "high": float(r[2]), "low": float(r[3]),
                        "close": float(r[4]), "volume": float(r[5]),
                        "close_time": int(r[0]) + 900000})
        return out[:-1]
    except Exception as e:
        raise RuntimeError(f"klines gagal untuk {symbol}: {e}") from e


def score(cand: dict, metrics: dict) -> float:
    """Legacy skor 0-100 (15m). Dibiarkan agar test lama tetap lulus."""
    vol_score = min(100.0, max(0.0, 20 + 25 * math.log10(cand["quote_vol"] / 1e6 + 1)))
    a = metrics["atr_pct"]
    if a < 0.1:
        volat_score = a * 200
    elif a <= 2.0:
        volat_score = 60 + (a - 0.1) / 1.9 * 40
    else:
        volat_score = max(20.0, 100 - (a - 2.0) * 15)
    m = abs(metrics["momentum"]) + abs(cand["change_pct"]) / 4
    mom_score = min(100.0, m * 12)
    vt = metrics["vol_trend"]
    trend_score = min(100.0, max(0.0, (vt - 0.5) * 100))
    return (vol_score * 0.30 + volat_score * 0.30
            + mom_score * 0.25 + trend_score * 0.15)


def scan(top_n: int = 20, min_vol: float = 5_000_000.0,
         analyze_limit: int = 40, quiet: bool = False) -> list[dict]:
    """Legacy scan 15m. Tetap ada agar compat; direkomendasikan
    menggunakan `scan_fast`."""
    tickers = None
    try:
        tickers = fetch_tickers()
    except SystemExit as e:
        print(f"  ⬇️ Binance block: {e}", flush=True)
        try:
            tickers = fetch_tickers_okx()
            if not quiet:
                print("  🔄 menggunakan OKX data", flush=True)
        except SystemExit as e2:
            raise SystemExit(f"scan gagal: Binance & OKX keduanya gagal: {e2}")
    cands = pick_candidates(tickers, min_vol, analyze_limit)
    if not quiet:
        print(f"menganalisis {len(cands)} coin teratas by volume...",
              flush=True)
    results = []
    for i, cand in enumerate(cands):
        m = analyze(cand["symbol"])
        if m is None:
            continue
        s = score(cand, m)
        results.append({**cand, **m, "score": s})
        if not quiet and (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(cands)}...", flush=True)
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_n]


def print_table(results: list[dict]) -> None:
    print()
    print(paint(f"{'#':<3} {'SYMBOL':<14} {'HARGA':<12} {'24H%':>8}  "
                f"{'VOL 24H':>9}  {'ATR%':>6}  {'SKOR':>5}", "bold", "cyan"))
    print(paint("-" * 66, "dim"))
    for i, r in enumerate(results, 1):
        chg = r["change_pct"]
        chg_s = f"{chg:+.1f}%"
        chg_c = "green" if chg >= 0 else "red"
        score_c = "green" if r["score"] >= 70 else ("yellow" if r["score"] >= 50 else "dim")
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}")
        plain = (f"{medal:<3} {r['symbol']:<14} {fmt_price(r['price']):<12} "
                 f"{chg_s:>8}  {_fmt_vol(r['quote_vol']):>9}  "
                 f"{r['atr_pct']:>5.2f}%  {r['score']:>5.0f}")
        print(paint(plain, score_c))
    print()
    if results:
        best = results[0]
        print(paint(f"⭐ Rekomendasi: {best['symbol']} (skor {best['score']:.0f}) — "
                    f"jalankan: python -m scalping {best['symbol']} --margin 100 "
                    f"--lev 10 --mode paper --margin-mode isolated", "green", "bold"))


def main(argv: Optional[list[str]] = None) -> int:
    args = list(argv or sys.argv[1:])
    top_n, min_vol, limit, grade = 20, 5_000_000.0, 50, "C"
    parallel, legacy = True, False
    it = iter(args)
    for a in it:
        if a == "--top":
            top_n = int(next(it, "20"))
        elif a == "--min-vol":
            min_vol = float(next(it, "5000000"))
        elif a == "--limit":
            limit = int(next(it, "50"))
        elif a == "--grade":
            grade = next(it, "C").upper()
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        elif a == "--legacy":
            legacy = True
        elif a == "--no-parallel":
            parallel = False
    t0 = time.time()
    print(paint("🔍 scan coin terbaik untuk scalping cepat (short-TF 1m/5m/15m)...",
                "cyan", "bold"), flush=True)
    if legacy:
        results = scan(top_n=top_n, min_vol=min_vol, analyze_limit=limit,
                       quiet=False)
        print_table(results)
    else:
        results = scan_fast(top_n=top_n, min_vol=min_vol, limit=limit,
                            parallel=parallel, min_grade=grade)
        print_table_fast(results)
    print(paint(f"selesai dalam {time.time() - t0:.1f}s", "dim"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
