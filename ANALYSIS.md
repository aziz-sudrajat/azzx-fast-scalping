# Review & Analyisis: azzx-fast-scalping

Diterima: `https://github.com/aziz-sudrajat/azzx-fast-scalping`
Lokasi: `/data/home/projects/azzx-fast-scalping`

## 1. Ringkasan arsitektur

```
scan.py            → screener Binance Futures (API publik, tanpa key)
scalping/
  config.py        → konfigurasi env/.env, mode paper/testnet/live
  indicators.py    → EMA/RSI/MACD/ATR/VWAP (stdlib) - fungsi murni
  signal.py        → ScalpEngine (MTF 1m/5m/15m), mengembalikan Signal|None
  backtest.py      → walk-forward backtest (taker assumption, slippage, fee)
  live_loop.py     → AlternatingTrader (paksa gantian) + SignalTrader (berbasis sinyal)
  risk.py          → RiskGuard (daily loss, cooldown, kill-switch, max-positions)
  broker.py        → PaperBroker + BinanceBroker (HMAC-SHA256 urllib)
  data.py          → BinanceData (klines publik, fail-closed)
  tracker.py       → PositionTracker (DCA anti-martingale)
  exchanges/
    binance.py     → BinanceFutures REST
    bybit.py       → Bybit v5
    okx.py         → OKX v5
    bitget.py      → Bitget v2
  web_ui.py        → (tidak ada; README merujuk scalping.webui)
```

## 2. Hasil test (baseline)

- 159 test lulus, 2 gagal
- 2 gagal adalah **integrasi/berbasis jaringan** (`test_pnl.py`, Bybit) — sebab HTTP 403
  CloudFront "block access from your country" pada sandbox (geoblock), **bukan bug kode**.
  Logik PnL di `BybitExchangeBroker.market_close` / `_track_close` sesuai harapan.

## 3. Bug yang ditemukan (kode) — SCANNER (multi-exchange)

| # | Lokasi | Jenis | Dampak | Status |
|---|--------|-------|--------|--------|
| B1 | `scanner.py` → `pick_candidates` | Keys mismatch | OKX data (`quote_vol`, `change_pct`, `price`) ≠ Binance (`quoteVolume`, `priceChangePercent`, `lastPrice`) → 0 candidates | Perbaiki: multi-key support |
| B2 | `scanner.py` → `fetch_tickers_okx` | URL & fields | OKX API: `instType=SWAP`, `vol24h`, `open24h`/`last` | Perbaiki: query parameter & field mapping |
| B3 | `scanner.py` → `_klines_impl` | OKX klines URL | OKX: `instId=..., bar=15m&limit=...` | Perbaiki: include parameters |
| B4 | `scanner.py` → `_norm_okx_symbol` | Symbol format | OKX `BTC-USDT-SWAP` → `BTCUSDT` (remove dash + SWAP) | Perbaiki: handle suffix |
| B5 | `scanner.py` → `scan()` | Single source | Fatal pas Binance blocked | Perbaiki: try Binance → OKX fallback |

## 4. Skenario perbaikan kualitas signal (ScalpEngine)

... (same as before) ...

## 4. Skenario perbaikan kualitas signal (ScalpEngine)

Skema sinyal saat ini: bias 15m (EMA21 + MACD hist) → trigger 1m (EMA9 cross EMA21) → konfirmasi 5m (EMA9 > EMA21) → filter ATR-percentile/volume/spread → strength dari MACD5m + RSI7.

### Skenario 1: ATR band filter (hindari whipsaw extreme)
**Masalah:** Sinyal hanya diblokir jika ATR rendah (< 20th percentile). ATR yang sangat
tinggi (krisis volatilitas) tetap menghasilkan sinyal, padahal harga likely over-extended.
**Solusi:** Tambahkan batas atas ATR percentile (`ATR_MAX_PERCENTILE`, default 85):
```
if pct_rank is None or pct_rank < ATR_MIN or pct_rank > ATR_MAX:
    return None  # terlalu rendah ATAU terlalu tinggi
```

### Skenario 2: Multi-timeframe volume confirmation
**Masalah:** Volume spike hanya diperiksa pada 1m. Tidak ada konfirmasi volume pada 5m/15m.
**Solusi:** Cek volume trend pada 5m (10 candle) & 15m (10 candle). Butuh keduanya naik.

### Skenario 3: RSI regime filter
**Masalah:** RSI7 hanya menambah strength bila ekstrem (<30 long / >70 short). Tidak ada filter
mencegah entry saat RSI berada di "zone overbought/oversold" yang justru sedang mean-reverting.
**Solusi:** Untuk long, tolak jika RSI7 > 70 (overbought). Untuk short, tolak jika RSI7 < 30.

### Skenario 4: Multi-timeframe strength score
**Masalah:** Strength hanya 1 + MACD5m + RSI7 (max 3). Tidak ada bobot dari 15m/5m alignment.
**Solusi:** Tingkatkan strength berdasarkan jumlah timeframe yang sesuai bias:
- 1m cross + 5m EMA bullish + 15m EMA rising → strength 2
- + macd5m searah + rsi ekstrem → strength 3

### Skenario 5: ATR-step unique ATR
**Masalah:** TP/SL dari ATR 1m (1.5x ATR entry). ATR 1m bisa sangat small untuk coin lain.
**Solusi:** Gunakan ATR 1m/5m/15m rata-rata (atau pilih ATR dari timeframe terkecil yang stabil).

### Skenario 6: Volatility regime (ATR percentile band + candle range)
**Masalah:** ATR percentile hanya melihat ATR, bukan range nyata bar.
**Solusi:** Tambahkan persentase range candle 1m (% true range) sebagai filter tambahan.

## 5. Perubahan yang akan dilakukan

1. **Improve `signal.py`:**
   - Tambahkan ATR band filter (min/max percentile)
   - Tambahkan multi-timeframe volume confirmation (5m, 15m)
   - Tambahkan improved strength scoring (multi-timeframe alignment)
   - Fix: h5/r7 computed before strength call
2. **Improve `config.py`:**
   - Tambahkan konstanta baru: `ATR_MAX_PERCENTILE`, `MIN_CANDLE_RANGE_PCT`
   - Update `_DEFAULT_KEYS` dan `validate()` untuk konstanta baru
3. **Improve `risk.py`:**
   - Konsistensi waktu WIB (sudah konsisten: `_today_wib()` + `time.time()` epoch)
4. **Add `scalping/web_ui.py`** (sederhana, stdlib http.server + async)
5. **Fix `scanner.py`:**
   - Timeout kustom untuk HTTP request (sudah ada default)
6. **Test:**
   - `pytest tests`: 159 passed, 2 failed (network/geoblock)
   - `scan.py` OKX fallback: ✅ Analisis 40 koin, rank top 10
   - Unit test: `fetch_tickers_okx` (490), `fetch_klines_15m_okx` (99)

## 6. Catatan final

- 159/161 test lulus (2 gagal saja network geoblock).
