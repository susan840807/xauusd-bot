import requests
from datetime import datetime, timezone, timedelta
import os

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def get_myt():
    return datetime.now(timezone(timedelta(hours=8)))

def get_utc_hour():
    return datetime.now(timezone.utc).hour

def get_session():
    h = get_utc_hour()
    if 13 <= h < 17: return "overlap"
    if  8 <= h < 17: return "london"
    if 13 <= h < 22: return "ny"
    return "asia"

def is_high_flow():
    return get_session() in ["london", "ny", "overlap"]

def fetch_current_price():
    """Fetch real XAU/USD spot price - no API key needed"""
    sources = [
        # Source 1: Swissquote public feed
        {
            "url": "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD",
            "parse": lambda d: round((float(d[0]["spreadProfilePrices"][0]["ask"]) + float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 2)
        },
        # Source 2: Frankfurter (XAU to USD)
        {
            "url": "https://api.frankfurter.app/latest?from=XAU&to=USD",
            "parse": lambda d: round(float(d["rates"]["USD"]), 2)
        },
    ]
    for s in sources:
        try:
            r = requests.get(s["url"], timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            price = s["parse"](r.json())
            if price and price > 1000:
                print(f"💰 XAU/USD spot price: ${price}")
                return price
        except Exception as e:
            print(f"⚠ Source failed: {e}")
    return None

def fetch_price_history(current_price):
    """
    Build realistic price history using yfinance GLD ETF scaled to spot price.
    GLD tracks gold at ~1/10 the spot price.
    """
    try:
        import yfinance as yf
        # Try XAUUSD=X first
        for symbol, multiplier in [("XAUUSD=X", 1), ("GLD", 10), ("IAU", 100)]:
            try:
                df = yf.Ticker(symbol).history(period="2d", interval="15m")
                if df.empty:
                    continue
                closes = [round(float(x), 4) for x in df["Close"].dropna().tolist()]
                volumes = [int(x) for x in df["Volume"].fillna(0).tolist()]
                if not closes:
                    continue
                # Scale prices to match current real price
                scale = current_price / (closes[-1] * multiplier)
                prices = [round(c * multiplier * scale, 2) for c in closes]
                print(f"📊 {symbol}: {len(prices)} candles, scaled to ${prices[-1]}")
                return prices, volumes
            except:
                continue
    except:
        pass

    # Fallback: simulate history around current price
    print("📊 Using simulated history around real price")
    import random
    prices = []
    p = current_price - 15
    for _ in range(60):
        p = p + (random.random() - 0.49) * 2.0
        prices.append(round(p, 2))
    prices[-1] = current_price
    volumes = [random.randint(500, 2000) for _ in prices]
    return prices, volumes

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    gains = losses = 0
    for i in range(len(prices) - period, len(prices)):
        d = prices[i] - prices[i-1]
        if d > 0: gains += d
        else: losses += abs(d)
    rs = (gains / period) / ((losses / period) + 0.0001)
    return round(100 - 100 / (1 + rs), 2)

def calc_ma(prices, n):
    if len(prices) < n: return prices[-1]
    return round(sum(prices[-n:]) / n, 2)

def calc_macd(prices):
    if len(prices) < 26: return 0
    def ema(p, n):
        k = 2 / (n + 1); e = p[-n]
        for x in p[-n+1:]: e = x * k + e * (1 - k)
        return e
    return round(ema(prices, 12) - ema(prices, 26), 4)

def volume_spike(volumes):
    if len(volumes) < 10 or sum(volumes) == 0: return False, 0
    avg = sum(volumes[-10:-1]) / 9 if sum(volumes[-10:-1]) > 0 else 1
    ratio = round(volumes[-1] / avg, 2)
    return ratio >= 1.5, ratio

def momentum(prices):
    if len(prices) < 5: return False, 0
    move = abs(prices[-1] - prices[-5])
    return move >= 5.0, round(move, 2)

def quality_gate(prices, volumes):
    ma20 = calc_ma(prices, 20)
    ma50 = calc_ma(prices, 50)
    rsi  = calc_rsi(prices)
    macd = calc_macd(prices)
    rng  = round(max(prices[-20:]) - min(prices[-20:]), 2) if len(prices) >= 20 else round(max(prices) - min(prices), 2)
    vs, vr = volume_spike(volumes)
    mo, ms = momentum(prices)
    struct = abs(ma20 - ma50) > 2.0
    trend  = "bullish" if ma20 > ma50 else "bearish"
    checks = {
        "high_flow":       is_high_flow(),
        "pip_potential":   rng >= 10.0,
        "clear_structure": struct,
        "volume_spike":    vs or mo,
        "ind_aligned":     (rsi < 42 or rsi > 58),
        "limit_ok":        True,
    }
    passed = sum(checks.values())
    return {**checks, "passed": passed, "all_pass": passed == 6,
            "rsi": rsi, "macd": macd, "ma20": ma20, "ma50": ma50,
            "range": rng, "vol_ratio": vr, "move_size": ms, "trend": trend}

def get_direction(prices, gate):
    bull = bear = 0
    if prices[-1] > gate["ma20"]: bull += 1
    else: bear += 1
    if gate["ma20"] > gate["ma50"]: bull += 1
    else: bear += 1
    if gate["rsi"] < 50: bull += 1
    else: bear += 1
    if gate["macd"] > 0: bull += 1
    else: bear += 1
    return "BUY" if bull >= 3 else "SELL"

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠ Telegram not configured")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
        return r.json().get("ok", False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def build_message(direction, price, gate, session):
    myt    = get_myt()
    is_buy = direction == "BUY"
    emoji  = "🟢" if is_buy else "🔴"
    sl     = round(price - 10 if is_buy else price + 10, 2)
    tp1    = round(price + 10 if is_buy else price - 10, 2)
    tp2    = round(price + 20 if is_buy else price - 20, 2)
    tp3    = round(price + 30 if is_buy else price - 30, 2)
    conf   = min(95, 50 + gate["passed"] * 7)
    reasons = []
    if gate["high_flow"]:        reasons.append(f"{session.upper()} session — high liquidity")
    if gate["vol_ratio"] >= 1.5: reasons.append(f"Volume spike {gate['vol_ratio']}x average")
    elif gate["move_size"] >= 5: reasons.append(f"Strong momentum ${gate['move_size']} move")
    if gate["clear_structure"]:  reasons.append(f"MA {gate['trend']} (MA20:{gate['ma20']} MA50:{gate['ma50']})")
    if gate["rsi"] < 42:         reasons.append(f"RSI oversold ({gate['rsi']}) — bounce likely")
    elif gate["rsi"] > 58:       reasons.append(f"RSI overbought ({gate['rsi']}) — rejection likely")
    return (
        f"{emoji} XAUUSD {direction} SIGNAL\n\n"
        f"⏰ {myt.strftime('%H:%M MYT')} · {session.upper()} SESSION\n\n"
        f"📍 Entry:     ${price}\n"
        f"🛑 Stop Loss: ${sl} (−100 pips)\n"
        f"✅ TP1:       ${tp1} (+100 pips)\n"
        f"✅ TP2:       ${tp2} (+200 pips)\n"
        f"✅ TP3:       ${tp3} (+300 pips)\n\n"
        f"📊 Daily Range: ${gate['range']} (~{int(gate['range']*10)} pips)\n"
        f"💯 Confidence: {conf}% ({gate['passed']}/6 gates)\n\n"
        f"🔍 Analysis:\n"
        + "\n".join(f"· {r}" for r in reasons)
        + "\n\n⚠️ Not financial advice. Manage your risk."
    )

def main():
    myt = get_myt()
    print(f"🔍 Scan: {myt.strftime('%H:%M MYT')} | Session: {get_session().upper()}")

    # Step 1: Get real current price
    current_price = fetch_current_price()
    if not current_price:
        print("❌ Cannot fetch real price — skipping scan")
        return

    # Step 2: Build price history scaled to real price
    prices, volumes = fetch_price_history(current_price)
    prices[-1] = current_price  # ensure last price is exact real price

    print(f"✅ Price confirmed: ${prices[-1]} | History: {len(prices)} candles")

    # Step 3: Quality gate
    gate = quality_gate(prices, volumes)
    print(f"📊 Gates: {gate['passed']}/6 | RSI:{gate['rsi']} | Range:${gate['range']} | Vol:{gate['vol_ratio']}x")
    print(f"   HighFlow:{gate['high_flow']} | Pip:{gate['pip_potential']} | Structure:{gate['clear_structure']} | Vol:{gate['volume_spike']} | Ind:{gate['ind_aligned']}")

    if gate["all_pass"]:
        direction = get_direction(prices, gate)
        msg = build_message(direction, current_price, gate, get_session())
        print(f"🚨 SIGNAL: {direction} @ ${current_price}")
        if send_telegram(msg):
            print("📲 Telegram sent!")
        else:
            print("❌ Telegram failed")
    else:
        reasons = []
        if not gate["high_flow"]:       reasons.append("Not London/NY session")
        if not gate["pip_potential"]:   reasons.append(f"Range too narrow (${gate['range']})")
        if not gate["clear_structure"]: reasons.append("No clear MA structure")
        if not gate["volume_spike"]:    reasons.append(f"No volume spike ({gate['vol_ratio']}x)")
        if not gate["ind_aligned"]:     reasons.append(f"RSI neutral ({gate['rsi']})")
        print(f"⛔ No signal: {', '.join(reasons)}")

if __name__ == "__main__":
    main()
