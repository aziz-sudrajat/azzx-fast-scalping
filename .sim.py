import math


def atr(v):
    n = len(v)
    out = [None] * n
    tr = []
    for i, c in enumerate(v):
        o = c["close"]
        hi = c["high"]
        lo = c["low"]
        tr.append(max(hi - lo, abs(hi - o), abs(lo - o)))
    prev = sum(tr[:14]) / 14
    out[13] = prev
    for i in range(14, n):
        prev = (prev * 13 + tr[i]) / 14
        out[i] = prev
    return out


def rank(a):
    a = [x for x in a if x is not None]
    return sum(1 for x in a if x <= a[-1]) / len(a) * 100


def cand(trend, amp, freq, spread, n=40):
    closes = [100 + trend * i + amp * math.sin(i * freq) for i in range(n)]
    kl = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        kl.append({"high": max(o, c) + spread, "low": min(o, c) - spread,
                   "close": c, "volume": 4000 if i >= 34 else 1000 + i})
    a = atr(kl)
    mom5 = (closes[-1] / closes[-6] - 1) * 100
    rng = sum(kl[-10 + k]["high"] - kl[-10 + k]["low"] for k in range(10)) / 10 / closes[-1] * 100
    sp = kl[-1]["volume"] / (sum(r["volume"] for r in kl[-22:-2]) / 20)
    return (round(rank(a), 1), round(mom5, 3), round(rng, 3), round(sp, 2))


for t, a, f, s in [(0.10, 0.3, 0.1, 0.3), (0.12, 0.3, 0.1, 0.3),
                   (0.10, 0.5, 0.1, 0.3), (0.12, 0.5, 0.1, 0.3)]:
    print("trend", t, "amp", a, "freq", f, "spread", s, cand(t, a, f, s))
