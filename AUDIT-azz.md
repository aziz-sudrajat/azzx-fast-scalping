# AUDIT SILANG — azzx-fast-scalping (auditor: azz)

**Tanggal:** 2026-10-18
**Lokasi:** `~/AgentOS_Memory/azzx-fast-scalping/`
**Metode:** independen, read-only (tidak ada kode yang diubah). Setiap temuan di bawah ini
terverifikasi dengan skrip reproduksi di `/tmp/` laptop atau kutipan kode file:baris.
Dugaan tanpa bukti tidak dilaporkan.
**Baseline test:** 153 passed, 0 failed (sebelum audit; test tidak diubah).

---

## TEMUAN TINGGI

### T1 — OKX: `sz` dikirim dalam base asset, padahal OKX SWAP membaca `sz` sebagai jumlah kontrak
- **File:** `scalping/exchanges/okx.py:116` — `"sz": f"{qty:.8f}"`
- **Alur:** `__main__.py:150` hitung `qty = notional / price` (base asset, mis. BTC) →
  `live_loop.py` → `market_open` → `sz` dikirim mentah tanpa konversi kontrak.
  Tidak ada penanganan `ctVal`/kontrak di seluruh repo (grep nihil).
- **Bukti** (`/tmp/repro_okx_sz.py`, lolos): qty 0.01231 BTC dikirim sebagai
  `sz="0.01231148"` → OKX membaca 0.0123 kontrak × ctVal 0.01 BTC =
  **0.000123 BTC aktual vs 0.01231 BTC yang diniatkan (100x lebih kecil)**.
- **Dampak:** tergantung `ctVal` simbol — BTC/ETH undersize; simbol dengan
  `ctVal > 1` bisa **oversize** melebihi niat user. **Menghalangi rilis live di OKX.**

### T2 — `market_close` gagal → posisi NYANGKUT terbuka tanpa proteksi apapun
- **File:** `scalping/live_loop.py` (~baris 220-228) — blok `except` setelah
  `market_close` hanya print `"DARURAT: cek posisi manual!"` lalu `break` keluar loop.
- **Bukti** (`/tmp/repro_stranded.py`, lolos): close selalu gagal → `trades_done=0`,
  posisi **tetap terbuka** di broker, loop berhenti total.
- **Dampak:** TP/SL bot bersifat sintetis (dimonitor di loop lokal,
  `live_loop.py:175-176, 207-214` — tidak pernah dipasang sebagai order exchange).
  Begitu loop berhenti, posisi yatim **tanpa stop-loss sama sekali**.
  Tidak ada retry close, tidak ada verifikasi state posisi, tidak ada alert.
  **Menghalangi rilis live.**

### T3 — Qty tidak dibulatkan ke stepSize/lot-size → order DITOLAK exchange
- **File:** `scalping/__main__.py:150` (`qty = notional / price`); `scalping/broker.py`
  hanya format string. Grep `stepsize|lot_size|minQty` di seluruh repo: **nihil**.
- **Bukti** (`/tmp/repro_lotsize.py`, lolos): qty `0.01231148` bukan kelipatan
  stepSize `0.001` (BTCUSDT) → Binance menolak dengan `Filter failure: LOT_SIZE`
  (error -1013). Berlaku juga untuk Bybit/Bitget (qty step).
- **Dampak:** di live, open gagal untuk kombinasi margin/leverage umum → loop hanya
  mencetak "open gagal" selamanya; **bot non-fungsional di live.** **Menghalangi rilis.**

### T4 — Restart proses membuat daily-loss guard buta (trade pasca-restart tercatat PnL 0.0)
- **File:** `scalping/exchanges/base.py:139-144` (`LocalPositionTracker._pos` murni in-memory);
  `scalping/live_loop.py:222-223, 237`.
- **Alur:** setelah restart, dict `_pos` kosong; `market_close()` tetap menutup posisi
  nyata di exchange (via `get_position`), tapi `_track_close()` mengembalikan
  `{"pnl_usd": 0.0, "untracked": True}` → `risk.record_trade(0.0)`.
- **Bukti** (`/tmp/repro_c4_restart_blind.py`, PASS di venv project):
  `pnl yang masuk risk.record_trade: 0.0` vs `pnl riil (rugi 10 USD): -10.0`.
- **Dampak:** rugi/untung riil trade tersebut **tidak tercatat** di guard — daily-loss
  limit bisa jebol diam-diam setelah restart. **Menghalangi rilis live.**

### T5 — Gagal `set_leverage`/`set_margin_type` hanya di-print, live tetap jalan
- **File:** `scalping/__main__.py:119-132` — kedua `try/except` hanya `print`
  peringatan lalu lanjut ke kalkulasi qty & `trader.run()`.
- **Dampak:** user minta `--lev 10`; jika call gagal (network error / posisi sudah ada),
  exchange tetap memakai leverage lama akun (**bisa 125x**) → risiko likuidasi di luar
  niat user. Jika margin-type gagal, posisi bisa berjalan cross-margin padahal user
  minta isolated. **Menghalangi rilis live.**

---

## TEMUAN SEDANG

### S1 — `_live_price` memakai harga basi (close candle 1m terakhir yang sudah close)
- **File:** `scalping/live_loop.py` (`_live_price`); kontrak di
  `scalping/exchanges/base.py` + `scalping/data.py:134-136` ("candle berjalan selalu dibuang").
- **Bukti** (`/tmp/repro_stale_price.py`, lolos): bot memakai `80000.0` (umur data
  55 detik) padahal harga real-time simulasi `81225.0` — selisih 1225 USD.
- **Dampak:** entry, level TP/SL, dan PnL tracking dihitung dari harga basi s/d ~60 detik;
  entry tercatat ≠ harga fill aktual → `risk.record_trade` bekerja di atas angka tak akurat.
  Untuk scalping, ini presisi eksekusi yang signifikan.

### S2 — `_track_open` menimpa (overwrite), bukan merata-rata: PnL dihitung dari entry TERAKHIR
- **File:** `scalping/exchanges/base.py:133-137` — assignment langsung
  `self._pos[symbol] = {...}`; open kedua tanpa close menimpa entry & qty pertama.
  Berbeda dengan `PositionTracker` (tracker.py) yang merata-rata dengan benar.
- **Bukti** (`/tmp/repro_c3_overwrite.py`, PASS): open 0.01 @80000 lalu 0.01 @81000,
  close @81500 → PnL terlapor **4.1875** vs seharusnya **20.0** (avg entry).
  PnL salah ini mengalir ke `risk.record_trade`.
- **Pemicu:** laten di alur saat ini (live_loop strictly alternating), tapi realistis via
  crash-restart + posisi nyangkut; class broker bersifat public API.

### S3 — Kill-switch satu arah: tidak ada `unkill()`, padahal docstring menjanjikan pencabutan eksplisit
- **File:** `scalping/risk.py:91-103` — `kill()` mengunci permanen; docstring
  `reset_day()`: *"pembunuhan manual hanya bisa dicabut eksplisit"* — tapi method
  pencabut tidak ada di seluruh file (107 baris, terbaca penuh).
- **Dampak:** kill tidak sengaja (mis. klik dashboard) → wajib restart proses.
  Termitigasi sebagian di webui (RiskGuard baru tiap Start), tapi CLI `live_loop`
  tidak punya jalan pulih.

---

## TEMUAN RENDAH

### R1 — Entry point `python -m scalping.live_loop` tanpa pre-flight & tanpa set leverage
- **File:** `scalping/live_loop.py` `main()` — tidak ada `get_balance()` pre-flight,
  tidak ada `set_leverage`/`set_margin_type` (berbeda dengan `__main__.py` yang punya).
- **Dampak:** kredensial salah baru ketahuan saat order pertama (retry selamanya);
  leverage = default akun apa adanya.

### R2 — `_window_upto` bocor data masa depan saat candle tanpa time key (backtest)
- **File:** `scalping/backtest.py:36-40` — fallback "best-effort bila tanpa waktu":
  jika candle 1m tidak punya time key, `t=None` → fungsi mengembalikan **seluruh**
  `k5m`/`k15m` termasuk bar masa depan → bias 15m & konfirmasi 5m dihitung dari
  data yang belum terjadi (lookahead bias).
- **Bukti** (`/tmp/audit_b_win.py`): 40 candle 15m (20 flat + 20 uptrend masa depan);
  windowed `t=19` → 20 candle (benar); fallback `t=None` → 40 candle (masa depan) → **LEAK TERBUKTI**.
- **Catatan:** tidak terjangkau via pipeline normal (`data.py get_klines` selalu
  menyertakan `open_time`); jebakan sunyi untuk backtest ad-hoc/sintetis.
  Saran: fail-closed (return list kosong → `evaluate` return None).

### R3 — Bonus MACD 5m mati sunyi saat data 5m 22–33 bar
- **File:** `scalping/signal.py:163-164` — `MIN_BARS_5M=22` mengizinkan evaluasi,
  tapi MACD(12,26,9) signal line baru valid di indeks 33 (butuh 34 bar).
  Akibatnya `h5=None` → bonus strength dari MACD 5m nonaktif tanpa peringatan
  pada 12 bar pertama data 5m.
- **Bukti** (`/tmp/audit_b_sig.py` T2): 22 bar → `h5=None`; 40 bar → ada nilai.
  Fail-safe (tidak crash), sembuh sendiri saat data terkumpul.
  Saran: naikkan `MIN_BARS_5M` ke 34 atau dokumentasikan.

### R4 — `config.validate()` tidak pernah dipanggil di production
- Dampak terbatas: `load()` hanya mengekspos `MODE` (divalidasi `_validate_mode`);
  selebihnya konstanta modul. Terkait: `RiskGuard()` selalu dikonstruksi dengan
  default — key config `MAX_DAILY_LOSS_USD`/`MAX_POSITIONS`/`COOLDOWN_SEC` tidak
  dikonsumsi siapa pun (grep: nol pemakaian di luar config.py) → dekoratif.
  Inkonsistensi minor: `validate()` membolehkan `MAX_DAILY_LOSS_USD=0`,
  `RiskGuard` menolaknya.

### R5 — webui: dropdown exchange mati + validasi lemah
- **File:** `scalping/webui.py` `_start_trader` menimpa parameter:
  `exchange = _state.get("exchange", "binance")` — selalu binance apapun pilihan UI.
- qty tidak divalidasi (`float(...)` mentah → qty negatif = loop "open gagal" abadi,
  aman dana tapi UX jelek); race check-then-act pada `_state["running"]` → dua POST
  `/api/start` berbarengan bisa menyalakan dua thread trader; pesan `/api/stop`
  menyesatkan ("diselesaikan dulu" padahal close market seketika via manual-stop).

### R6 — Scanner: denylist stablecoin tidak lengkap
- **File:** `scalping/scanner.py:33` — USDD, USDJ, XAUT, PAXG, BFUSD lolos filter.
- Termitigasi oleh skoring ATR% (stablecoin ≈ skor volatilitas ~2/100).
  Request aman: timeout 15 dtk + User-Agent (`scanner.py:37-38`); candle berjalan
  dibuang (`:91`); leveraged-token filter (UP/DOWN/BULL/BEAR) benar.

---

## INFO (bukan temuan)

- **I1 — `PositionTracker` (tracker.py) benar per spec** (avg = cost/qty, DCA
  anti-martingale: searah saja, qty fill ≤ qty awal, level ≤ max) **TAPI dead code** —
  nol pemanggil di production (hanya docstring broker.py yang menyebutnya).
  Jalur live memakai `LocalPositionTracker` (temuan T4 & S2 di atas).

---

## TERVERIFIKASI AMAN (dengan bukti, bukan sekadar klaim)

- **Jalur uang:** format qty desimal polos di semua exchange (tidak ada notasi ilmiah);
  side mapping benar (Binance BUY/SELL, OKX buy/sell, Bybit Buy/Sell, Bitget buy/sell);
  signing HMAC — body yang di-sign IDENTIK dengan body yang dikirim di keempat
  exchange; `get_position` side mapping benar (Binance tanda `positionAmt`, OKX
  `posSide` long/short/net, Bybit `Buy`/`Sell`, Bitget `holdSide` long/short);
  `get_balance` fail-fast di keempat exchange (raise saat key salah);
  guard `--i-understand` ada di kedua CLI; fee tidak double-count
  (PaperBroker fee di live_loop, broker real fee di `_track_close` — saling eksklusif);
  `market_close` memakai `track=False` untuk order penutup → entry asli tidak tertimpa.
- **Strategi:** indikator EMA/RSI(Wilder)/ATR/VWAP/CVD cocok hitungan tangan
  (10/10 cek, `/tmp/audit_b_ind.py`); strength konsisten 1–3
  (`min(strength,3)`, satu-satunya konstruksi `Signal(`); tanpa lookahead di jalur
  sinyal normal (backtest potong `w1=k1m[:i+1]`, window by time); SL diprioritaskan
  saat TP & SL tersentuh satu bar (`reason=sl` terbukti runtime); fee + slippage di
  kedua sisi (numerik eksak cocok hitungan tangan); TP/SL/time-stop baru dicek
  mulai bar berikutnya.
- **Risk:** daily PnL reset otomatis ganti hari (terbukti simulasi, termasuk WIB);
  `check()` tiap iterasi loop, `record_trade()` tiap close; kill-switch bekerja;
  **pemisahan paper/live bersih** — `PaperBroker` murni in-memory, hanya string
  exakt `"live"` yang mencapai live keys (varian case/typo jatuh ke testnet);
  webui menolak `"live"` server-side; `run.py` menolak mode non-paper.
- **Operasional:** `data.py` hanya candle CLOSED (`candles[:-1]`), fail-closed total
  (semua error → RuntimeError, nol data sintetis); webui bind 127.0.0.1, SSE tidak
  memuat secret, live trading dikunci server-side.
- **Rilis:** tidak ada secret hardcode (semua key via parameter/env);
  `git status` bersih; `git log` wajar (commit audit/fix berurutan).

---

## Verdict

**PERLU PERBAIKAN** — temuan yang menghalangi rilis live: **T1, T2, T3, T4, T5**.
Inti strategi (indikator, MTF, backtest) dan guard dasar (daily reset, kill-switch,
pemisahan paper/live) terverifikasi benar. Tapi 5 temuan TINGGI di jalur
uang/risk adalah cacat yang bisa menyebabkan salah ukuran order (T1), dana
tidak terlindungi (T2), bot non-fungsional (T3), guard buta (T4), dan leverage
liar (T5). Perbaiki kelimanya, lalu audit ulang seksi A & C sebelum live.

---

## Daftar file yang dibaca

`scalping/exchanges/base.py`, `scalping/exchanges/binance.py`,
`scalping/exchanges/okx.py`, `scalping/exchanges/bybit.py`,
`scalping/exchanges/bitget.py`, `scalping/exchanges/__init__.py`,
`scalping/broker.py`, `scalping/live_loop.py`, `scalping/__main__.py`,
`scalping/run.py`, `scalping/data.py`, `scalping/signal.py`,
`scalping/backtest.py`, `scalping/indicators.py`, `scalping/risk.py`,
`scalping/tracker.py`, `scalping/config.py`, `scalping/scanner.py`,
`scan.py`, `scalping/webui.py`, `scalping/chart.py` (daftar, tidak dibedah dalam).

## Skrip reproduksi (di `/tmp/` laptop, read-only terhadap project)

`/tmp/repro_okx_sz.py`, `/tmp/repro_lotsize.py`, `/tmp/repro_stale_price.py`,
`/tmp/repro_stranded.py`, `/tmp/repro_c1_rollover.py`,
`/tmp/repro_c3_overwrite.py`, `/tmp/repro_c4_restart_blind.py`,
`/tmp/audit_b_ind.py`, `/tmp/audit_b_sig.py`, `/tmp/audit_b_win.py`
(semua lolos saat dijalankan).
