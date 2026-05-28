import requests
import yfinance as yf
from datetime import datetime, timezone, timedelta
import os

# ── CONFIG ───────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MAX_SIGNALS_PER_DAY = 3

# ── TIME ─────────────────────────────────────────────────────
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

# ── FETCH REAL PRICE DATA ─────────────────────────────────────
def fetch_real_prices():
    # Try multiple tickers to get correct ~$4400+ gold price
    tickers = ["XAUUSD=X", "GC=F", "IAU", "GLD"]

    for symbol in tickers:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="2d", interval="15m")

            if df.empty:
                print(f"⚠ {symbol}: empty data")
                continue

            prices  = [round(float(x), 2) for x in df["Close"].dropna().tolist()]
            volumes = [int(x) for x in df["Volume"].fillna(0).tolist()]

            if not prices:
                continue

            last = prices[-1]
            print(f"📊 {symbol}: ${last} ({len(prices)} candles)")

            # XAUUSD should be ~3000-5000, GLD ~300-500, IAU ~30-50
            if symbol in ["XAUUSD=X", "GC=F"] and last > 2000:
                print(f"✅ Using {symbol}: ${last}")
                return prices, volumes, symbol
            elif symbol == "GLD" and last > 200:
                # GLD = gold price / 10 approx
                prices = [round(p * 10, 2) for p in prices]
                print(f"✅ Using GLD (x10): ${prices[-1]}")
                return prices, volumes, symbol
            elif symbol == "IAU" and last > 20:
                # IAU = gold price / 100 approx
                prices = [round(p * 100, 2) for p in prices]
                print(f"✅ Using IAU (x100): ${prices[-1]}")
                return prices, volumes, symbol

        except Exception as e:
            print(f"⚠ {symbol} error: {e}")
            continue

    # Last resort: fetch spot price from API
    try:
        r = requests.get(
            "https://query2.finance.yahoo.com/v8/finance/chart/XAUUSD=X",
            params={"interval": "15m", "range": "2d"},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=15
        )
        data = r.json()
        result = data["chart"]["result"][0]
        closes  = [round(float(c), 2) for c in result["indicators"]["quote"][0]["close"] if c]
        volumes = [int(v) if v else 0 for v in result["indicators"]["quote"][0]["volume"] if v is not None]
        if closes:
            print(f"✅ Fallback XAUUSD=X API: ${closes[-1]}")
            return closes, volumes, "XAUUSD=X"
    except Exception as e:
        print(f"⚠ API fallback failed: {e}")

    print("❌ All sources failed")
    return None, None, None

# ── INDICATORS ────────────────────────────────────────────────
def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains = losses = 0
    for i in range(len(prices) - period, len(prices)):
        d = prices[i] - prices[i-1]
        if d > 0: gains += d
        else: losses += abs(d)
    rs = (gains / period) / ((losses / period) + 0.0001)
    return round(100 - 100 / (1 + rs), 2)

def calc_ma(prices, n):
    if len(prices) < n:
        return prices[-1]
    return round(sum(prices[-n:]) / n, 2)

def calc_macd(prices):
    if len(prices) < 26:
        return 0
    def ema(p, n):
        k = 2 / (n + 1)
        e = p[-n]
        for x in p[-n+1:]:
            e = x * k + e * (1 - k)
        return e
    return round(ema(prices, 12) - ema(prices, 26), 4)

def detect_volume_spike(volumes):
    if len(volumes) < 10:
        return False, 0
    recent = volumes[-1]
    avg    = sum(volumes[-10:-1]) / 9 if sum(volumes[-10:-1]) > 0 else 1
    ratio  = round(recent / avg, 2)
    return ratio >= 1.5, ratio

def detect_momentum(prices):
    if len(prices) < 5:
        return False, 0
    move = abs(prices[-1] - prices[-5])
    return move >= 5.0, round(move, 2)

# ── QUALITY GATE ─────────────────────────────────────────────
def quality_gate(prices, volumes):
    ma20  = calc_ma(prices, 20)
    ma50  = calc_ma(prices, 50)
    rsi   = calc_rsi(prices)
    macd  = calc_macd(prices)
    rng   = round(max(prices[-20:]) - min(prices[-20:]), 2) if len(prices) >= 20 else round(max(prices) - min(prices), 2)

    vol_spike, vol_ratio = detect_volume_spike(volumes)
    momentum,  move_size = detect_momentum(prices)
    structure            = abs(ma20 - ma50) > 2.0
    trend                = "bullish" if ma20 > ma50 else "bearish"

    checks = {
        "high_flow":       is_high_flow(),
        "pip_potential":   rng >= 10.0,
        "clear_structure": structure,
        "volume_spike":    vol_spike or momentum,
        "ind_aligned":     (rsi < 42 or rsi > 58),
        "limit_ok":        True,
    }

    passed = sum(checks.values())
    return {
        **checks,
        "passed": passed, "all_pass": passed == 6,
        "rsi": rsi, "macd": macd, "ma20": ma20, "ma50": ma50,
        "range": rng, "vol_ratio": vol_ratio, "move_size": move_size, "trend": trend,
    }

# ── DIRECTION ─────────────────────────────────────────────────
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

# ── TELEGRAM ─────────────────────────────────────────────────
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

# ── MESSAGE ───────────────────────────────────────────────────
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
    if gate["high_flow"]:
        reasons.append(f"{session.upper()} session — high liquidity")
    if gate["vol_ratio"] >= 1.5:
        reasons.append(f"Volume spike {gate['vol_ratio']}x above average")
    elif gate["move_size"] >= 5:
        reasons.append(f"Strong momentum — ${gate['move_size']} move detected")
    if gate["clear_structure"]:
        reasons.append(f"MA structure {gate['trend']} (MA20:{gate['ma20']} / MA50:{gate['ma50']})")
    if gate["rsi"] < 42:
        reasons.append(f"RSI oversold ({gate['rsi']}) — bounce likely")
    elif gate["rsi"] > 58:
        reasons.append(f"RSI overbought ({gate['rsi']}) — rejection likely")

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

# ── MAIN ─────────────────────────────────────────────────────
def main():
    myt = get_myt()
    print(f"🔍 Scan: {myt.strftime('%H:%M MYT')} | Session: {get_session().upper()}")

    prices, volumes, source = fetch_real_prices()

    if not prices or len(prices) < 5:
        print("❌ Not enough price data")
        return

    print(f"💰 XAUUSD: ${prices[-1]} (source: {source}) | Candles: {len(prices)}")

    gate = quality_gate(prices, volumes)
    print(f"✅ Gates: {gate['passed']}/6 | RSI: {gate['rsi']} | Range: ${gate['range']} | Vol: {gate['vol_ratio']}x")

    if gate["all_pass"]:
        direction = get_direction(prices, gate)
        price     = prices[-1]
        msg       = build_message(direction, price, gate, get_session())
        print(f"\n🚨 SIGNAL: {direction} XAUUSD @ ${price}")
        if send_telegram(msg):
            print("📲 Telegram sent successfully!")
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

 

