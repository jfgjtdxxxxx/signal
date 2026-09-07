"""
ربات تحلیل‌گر کریپتو (Crypto Signal Bot)
----------------------------------------
هر بار اجرا میشه (هر ۶ ساعت):
  0) عملکرد سیگنال‌های ۶ ساعت قبل رو چک می‌کنه (تارگت/استاپ خورده یا نه، الان سود/ضرر چقدره)
  1) لیست ۸۰ کوین برتر از CoinGecko میگیره
  2) داده قیمت ۷ روز اخیر (ساعتی) از CoinGecko میگیره و تحلیل تکنیکال میزنه
     (RSI, MACD, SMA20/50, نوسان قیمت، حجم نسبت به میانگین)
  3) اخبار اخیر مرتبط با هر کوین رو از RSS های خبری چک می‌کنه
  4) احساسات اجتماعی (Bullish/Bearish) رو از StockTwits میگیره
  5) یه امتیاز داخلی برای هر کوین حساب می‌کنه و فقط قوی‌ترین ۴-۵ سیگنال
     (چه خرید/Long چه فروش/Short) رو با هدف (TP) و حد ضرر (SL) نگه می‌داره
  6) نتیجه رو به صورت پیام تلگرام میفرسته و سیگنال‌های انتخاب‌شده رو ذخیره می‌کنه
     (تو data/last_signals.json) تا اجرای بعدی بتونه عملکردشونو بسنجه

نکته فنی: قبلاً از Binance برای داده تکنیکال استفاده می‌شد ولی API بایننس از IP
سرورهای GitHub Actions مسدوده (محدودیت جغرافیایی)، پس همه‌چی از CoinGecko میاد.

⚠️ توجه مهم:
این ابزار فقط داده‌ها رو جمع و تحلیل می‌کنه و یک سیستم قانون‌محور (rule-based) ساده‌ست.
هیچ تضمینی برای سودآوری وجود نداره. همیشه با ریسک خودتون معامله کنید و این رو
به‌عنوان مشاوره مالی در نظر نگیرید.
"""

import os
import time
import json
import requests
import pandas as pd
import feedparser
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator

# ---------------- تنظیمات ----------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TOP_N = 80                 # تعداد کوین‌های برتر
MARKET_CHART_DAYS = 7      # بازه داده برای تحلیل تکنیکال (۷ روز = کندل‌های ساعتی، مناسب ترید کوتاه‌مدت)
TOP_SIGNALS_COUNT = 5      # حداکثر تعداد سیگنالی که هر بار نمایش داده میشه
MIN_SCORE_THRESHOLD = 1.5  # حداقل امتیاز (قدر مطلق) برای این‌که یه کوین اصلاً "سیگنال قوی" حساب بشه
REQUEST_TIMEOUT = 15
REQUEST_DELAY = 1.5        # فاصله بین درخواست‌ها برای رعایت rate-limit رایگان CoinGecko
SIGNALS_FILE = "data/last_signals.json"  # محل ذخیره سیگنال‌های هر اجرا برای مقایسه با اجرای بعدی

NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (crypto-signal-bot)"}


# ---------------- گام ۱: گرفتن لیست کوین‌ها ----------------
def get_top_coins(n=TOP_N):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": n,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }
    r = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------------- گام ۲: تحلیل تکنیکال از CoinGecko ----------------
# نکته: قبلاً از Binance استفاده می‌شد ولی API بایننس از IP سرورهای GitHub Actions
# مسدوده (محدودیت جغرافیایی) و همیشه جواب رد می‌داد. حالا همه‌چی از CoinGecko میاد.
def get_technical_analysis(coin_id):
    """coin_id همون شناسه CoinGecko کوینه (مثل 'bitcoin', 'chainlink')"""
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": MARKET_CHART_DAYS}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        prices = data.get("prices", [])
        volumes = data.get("total_volumes", [])
        if not prices or len(prices) < 30:
            return None

        close = pd.Series([p[1] for p in prices])
        volume = pd.Series([v[1] for v in volumes]) if volumes else pd.Series([0] * len(close))

        rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
        macd_ind = MACD(close)
        macd_line = macd_ind.macd().iloc[-1]
        macd_signal = macd_ind.macd_signal().iloc[-1]
        sma20 = SMAIndicator(close, window=20).sma_indicator().iloc[-1]
        sma50 = SMAIndicator(close, window=min(50, len(close) - 1)).sma_indicator().iloc[-1]

        # چون CoinGecko کندل High/Low نمیده (فقط قیمت لحظه‌ای)، ATR واقعی قابل محاسبه نیست.
        # به‌جاش از میانگین تغییرات قیمت بین نقطه‌ها (close-to-close) به‌عنوان تقریب نوسان استفاده می‌کنیم.
        volatility = close.diff().abs().rolling(14).mean().iloc[-1]

        current_price = close.iloc[-1]
        avg_volume = volume.iloc[-20:].mean()
        last_volume = volume.iloc[-1]
        volume_ratio = (last_volume / avg_volume) if avg_volume > 0 else 1.0

        return {
            "rsi": round(float(rsi), 1),
            "macd_bullish": bool(macd_line > macd_signal),
            "price_above_sma20": bool(current_price > sma20),
            "sma20_above_sma50": bool(sma20 > sma50),
            "volume_ratio": round(float(volume_ratio), 2),
            "current_price": float(current_price),
            "atr": float(volatility) if volatility == volatility else None,  # چک NaN
        }
    except Exception:
        return None


# ---------------- گام ۳: اخبار ----------------
def get_news_mentions(coin_name, coin_symbol, feeds=NEWS_FEEDS):
    mentions = []
    name_l = coin_name.lower()
    sym_l = coin_symbol.lower()
    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:40]:
                title = entry.get("title", "").lower()
                if name_l in title or f" {sym_l} " in f" {title} ":
                    mentions.append(entry.get("title"))
        except Exception:
            continue
    return mentions[:5]


# ---------------- گام ۴: احساسات اجتماعی (StockTwits) ----------------
def get_social_sentiment(symbol):
    """
    از StockTwits (رایگان، بدون نیاز به کلید) برای گرفتن احساسات استفاده می‌کنیم.
    این جایگزین توییتره چون API رسمی X هزینه‌بره.
    نکته: اگر StockTwits فرمت/محدودیتشو عوض کنه، این تابع باید آپدیت بشه.
    """
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol.upper()}.X.json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        messages = data.get("messages", [])
        if not messages:
            return None
        bullish = 0
        bearish = 0
        for m in messages:
            sentiment = (m.get("entities", {}) or {}).get("sentiment")
            if sentiment:
                label = sentiment.get("basic")
                if label == "Bullish":
                    bullish += 1
                elif label == "Bearish":
                    bearish += 1
        total = bullish + bearish
        if total == 0:
            return None
        return {
            "bullish_pct": round(bullish / total * 100, 1),
            "bearish_pct": round(bearish / total * 100, 1),
            "sample_size": total,
            "message_volume": len(messages),
        }
    except Exception:
        return None


# ---------------- گام ۵: امتیازدهی نهایی ----------------
def score_coin(market_data, ta_data, news, social):
    score = 0
    reasons = []

    price_change = market_data.get("price_change_percentage_24h") or 0
    if price_change > 5:
        score += 1
        reasons.append(f"رشد ۲۴س: +{price_change:.1f}%")
    elif price_change < -5:
        score -= 1
        reasons.append(f"افت ۲۴س: {price_change:.1f}%")

    if ta_data:
        if ta_data["rsi"] < 30:
            score += 1
            reasons.append(f"RSI اشباع فروش ({ta_data['rsi']})")
        elif ta_data["rsi"] > 70:
            score -= 1
            reasons.append(f"RSI اشباع خرید ({ta_data['rsi']})")

        if ta_data["macd_bullish"]:
            score += 1
            reasons.append("MACD صعودی")
        else:
            score -= 0.5

        if ta_data["price_above_sma20"] and ta_data["sma20_above_sma50"]:
            score += 1
            reasons.append("روند صعودی (قیمت بالای SMA20>SMA50)")

        if ta_data["volume_ratio"] > 1.5:
            score += 1
            reasons.append(f"جهش حجم ({ta_data['volume_ratio']}x)")

    if news:
        score += 0.5
        reasons.append(f"{len(news)} خبر مرتبط")

    if social:
        if social["bullish_pct"] > 65:
            score += 1
            reasons.append(f"احساسات بازار: {social['bullish_pct']}% صعودی")
        elif social["bearish_pct"] > 65:
            score -= 1
            reasons.append(f"احساسات بازار: {social['bearish_pct']}% نزولی")

    return round(score, 1), reasons


# ---------------- محاسبه هدف (TP) و حد ضرر (SL) برای ترید کوتاه‌مدت (حداکثر ۱ روزه) ----------------
def calc_trade_levels(ta_data, direction):
    """
    بر اساس ATR (نوسان واقعی همون کوین) سطوح رو حساب می‌کنه - نه درصد ثابت.
    نسبت ریسک‌به‌ریوارد اینجا 1:1.5 در نظر گرفته شده (استاندارد رایج).
    direction: 'long' (خرید) یا 'short' (فروش)
    """
    if not ta_data or not ta_data.get("atr") or not ta_data.get("current_price"):
        return None

    price = ta_data["current_price"]
    atr = ta_data["atr"]

    if direction == "long":
        tp = price + (atr * 1.5)
        sl = price - (atr * 1.0)
    else:  # short
        tp = price - (atr * 1.5)
        sl = price + (atr * 1.0)

    tp_pct = (tp - price) / price * 100
    sl_pct = (sl - price) / price * 100

    return {
        "entry": round(price, 6),
        "tp": round(tp, 6),
        "sl": round(sl, 6),
        "tp_pct": round(tp_pct, 2),
        "sl_pct": round(sl_pct, 2),
    }


def fmt_price(p):
    """قیمت رو با تعداد رقم اعشار مناسب فرمت می‌کنه (کوین‌های ارزون رقم اعشار بیشتری لازم دارن)"""
    if p >= 100:
        return f"{p:,.2f}"
    elif p >= 1:
        return f"{p:,.4f}"
    else:
        return f"{p:.6f}"


# ---------------- ذخیره و بررسی عملکرد سیگنال‌های اجرای قبلی ----------------
def load_previous_signals():
    """سیگنال‌های ذخیره‌شده از اجرای قبلی رو می‌خونه (اگه وجود نداشته باشه یعنی اولین اجراست)"""
    try:
        with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("signals", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_signals(strong_bullish, strong_bearish):
    """سیگنال‌های همین اجرا رو ذخیره می‌کنه تا اجرای بعدی بتونه باهاش مقایسه کنه"""
    signals = []
    for r in strong_bullish:
        levels = calc_trade_levels(r.get("ta_data"), "long")
        if levels:
            signals.append({
                "id": r["id"], "symbol": r["symbol"], "name": r["name"], "direction": "long",
                "entry": levels["entry"], "tp": levels["tp"], "sl": levels["sl"],
                "generated_at": time.time(),
            })
    for r in strong_bearish:
        levels = calc_trade_levels(r.get("ta_data"), "short")
        if levels:
            signals.append({
                "id": r["id"], "symbol": r["symbol"], "name": r["name"], "direction": "short",
                "entry": levels["entry"], "tp": levels["tp"], "sl": levels["sl"],
                "generated_at": time.time(),
            })

    os.makedirs(os.path.dirname(SIGNALS_FILE), exist_ok=True)
    with open(SIGNALS_FILE, "w", encoding="utf-8") as f:
        json.dump({"generated_at": time.time(), "signals": signals}, f, ensure_ascii=False, indent=2)


def get_current_prices(coin_ids):
    """قیمت لحظه‌ای چند کوین رو با CoinGecko تو یه درخواست می‌گیره (بر اساس coin_id، نه سیمبل)"""
    if not coin_ids:
        return {}
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": ",".join(coin_ids), "vs_currencies": "usd"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return {coin_id: info["usd"] for coin_id, info in data.items() if "usd" in info}
    except Exception:
        return {}


def evaluate_previous_signals(previous_signals, current_prices):
    """
    برای هر سیگنال قبلی چک می‌کنه: تارگت خورده، استاپ خورده، یا هنوز بازه (و الان چند درصد سود/ضرره)
    """
    evaluated = []
    for sig in previous_signals:
        current_price = current_prices.get(sig.get("id"))
        if current_price is None:
            continue

        entry, tp, sl = sig["entry"], sig["tp"], sig["sl"]
        direction = sig["direction"]

        if direction == "long":
            pnl_now_pct = (current_price - entry) / entry * 100
            if current_price >= tp:
                status = "tp_hit"
                pnl_pct = (tp - entry) / entry * 100
            elif current_price <= sl:
                status = "sl_hit"
                pnl_pct = (sl - entry) / entry * 100
            else:
                status = "open"
                pnl_pct = pnl_now_pct
        else:  # short
            pnl_now_pct = (entry - current_price) / entry * 100
            if current_price <= tp:
                status = "tp_hit"
                pnl_pct = (entry - tp) / entry * 100
            elif current_price >= sl:
                status = "sl_hit"
                pnl_pct = (entry - sl) / entry * 100
            else:
                status = "open"
                pnl_pct = pnl_now_pct

        evaluated.append({
            **sig,
            "current_price": current_price,
            "status": status,
            "pnl_pct": round(pnl_pct, 2),
        })
    return evaluated


def build_performance_section(evaluated_signals):
    """بخش بررسی عملکرد سیگنال‌های اجرای قبلی رو می‌سازه - هر خط جدا، مرتب"""
    if not evaluated_signals:
        return None

    status_map = {
        "tp_hit": ("✅", "هدف خورد"),
        "sl_hit": ("❌", "استاپ خورد"),
        "open": ("⏳", "هنوز باز"),
    }

    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("📈 <b>بررسی عملکرد سیگنال‌های ۶ ساعت قبل</b>")
    lines.append("━━━━━━━━━━━━━━━━━━")

    wins = sum(1 for s in evaluated_signals if s["status"] == "tp_hit")
    losses = sum(1 for s in evaluated_signals if s["status"] == "sl_hit")
    still_open = sum(1 for s in evaluated_signals if s["status"] == "open")
    lines.append(f"نتیجه کلی: <code>{wins}</code> هدف خورده | <code>{losses}</code> استاپ خورده | <code>{still_open}</code> باز")
    lines.append("")

    for s in evaluated_signals:
        emoji, label = status_map[s["status"]]
        direction_label = "Long" if s["direction"] == "long" else "Short"
        sign = "+" if s["pnl_pct"] >= 0 else ""
        lines.append(f"{emoji} <b>{s['symbol']}</b> ({direction_label}) — {label}")
        lines.append(f"   ورود: <code>${fmt_price(s['entry'])}</code> ← الان: <code>${fmt_price(s['current_price'])}</code>")
        lines.append(f"   نتیجه: <code>{sign}{s['pnl_pct']:.2f}%</code>")
        lines.append("")

    return "\n".join(lines)


# ---------------- گام ۶: ساخت و ارسال پیام تلگرام ----------------
def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ TELEGRAM_BOT_TOKEN یا TELEGRAM_CHAT_ID تنظیم نشده.")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # تلگرام هر پیام حداکثر ۴۰۹۶ کاراکتر - اگه بلندتر بود تیکه‌تیکه می‌فرستیم
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)]
    for chunk in chunks:
        try:
            requests.post(url, data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            print(f"خطا در ارسال به تلگرام: {e}")
        time.sleep(1)


def build_coin_block(r, direction):
    """
    یه بلوک کامل برای یه کوین می‌سازه: قیمت، هدف/ضرر، و دلایل (هر کدوم خط جدا).
    direction: 'long' برای سیگنال صعودی، 'short' برای نزولی
    """
    emoji = "🟢" if direction == "long" else "🔴"
    lines = [f"{emoji} <b>{r['symbol']}</b> ({r['name']})"]

    levels = calc_trade_levels(r.get("ta_data"), direction)
    if levels:
        label = "خرید (Long)" if direction == "long" else "فروش (Short)"
        lines.append(f"   ⚡️ نوع معامله: {label} | حداکثر افق زمانی: ۱ روز")
        lines.append(f"   💰 ورود: <code>${fmt_price(levels['entry'])}</code>")
        lines.append(f"   🎯 هدف (TP): <code>${fmt_price(levels['tp'])}</code> (<code>{levels['tp_pct']:+.2f}%</code>)")
        lines.append(f"   🛑 حد ضرر (SL): <code>${fmt_price(levels['sl'])}</code> (<code>{levels['sl_pct']:+.2f}%</code>)")
    else:
        lines.append("   ⚠️ داده کافی برای محاسبه هدف/حد ضرر این کوین موجود نبود.")

    lines.append("   دلایل:")
    for reason in r["reasons"][:4]:
        lines.append(f"   • {reason}")

    return "\n".join(lines)


def select_top_signals(results):
    """
    فقط قوی‌ترین سیگنال‌ها رو انتخاب می‌کنه (چه صعودی چه نزولی) - حداکثر TOP_SIGNALS_COUNT تا،
    و فقط اگه امتیازشون از MIN_SCORE_THRESHOLD بیشتر باشه (تا سیگنال ضعیف زورکی نمایش داده نشه).
    """
    candidates = [r for r in results if abs(r["score"]) >= MIN_SCORE_THRESHOLD and r.get("ta_data")]
    candidates.sort(key=lambda r: abs(r["score"]), reverse=True)
    top = candidates[:TOP_SIGNALS_COUNT]

    bullish = [(r, "long") for r in top if r["score"] > 0]
    bearish = [(r, "short") for r in top if r["score"] < 0]
    return bullish, bearish


def build_report(results, performance_section=None):
    bullish, bearish = select_top_signals(results)

    lines = []
    lines.append("📊 <b>گزارش تحلیل بازار کریپتو</b>")
    lines.append(f"🕐 <code>{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}</code>")
    lines.append(f"🔍 اسکن <code>{len(results)}</code> کوین برتر")
    lines.append("")

    if performance_section:
        lines.append(performance_section)

    if bullish:
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("🟢 <b>قوی‌ترین سیگنال‌های خرید (Long)</b>")
        lines.append("━━━━━━━━━━━━━━━━━━")
        for r, direction in bullish:
            lines.append(build_coin_block(r, direction))
            lines.append("")

    if bearish:
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("🔴 <b>قوی‌ترین سیگنال‌های فروش (Short)</b>")
        lines.append("━━━━━━━━━━━━━━━━━━")
        for r, direction in bearish:
            lines.append(build_coin_block(r, direction))
            lines.append("")

    if not bullish and not bearish:
        lines.append("😐 این ۶ ساعت هیچ سیگنال قوی و قابل‌اتکایی تو ۸۰ کوین برتر پیدا نشد.")
        lines.append("")

    lines.append("⚠️ این تحلیل صرفاً جمع‌بندی داده‌هاست، مشاوره مالی نیست و TP/SL بر پایه نوسان اخیر قیمت محاسبه شده — نه پیش‌بینی قطعی.")
    return "\n".join(lines)


def main():
    # گام ۰: بررسی عملکرد سیگنال‌های اجرای قبلی (اگه وجود داشته باشن)
    previous_signals = load_previous_signals()
    performance_section = None
    if previous_signals:
        print(f"در حال بررسی عملکرد {len(previous_signals)} سیگنال قبلی...")
        ids_needed = list({s["id"] for s in previous_signals if "id" in s})
        current_prices = get_current_prices(ids_needed)
        evaluated = evaluate_previous_signals(previous_signals, current_prices)
        performance_section = build_performance_section(evaluated)

    print("در حال گرفتن لیست کوین‌ها...")
    coins = get_top_coins(TOP_N)
    results = []

    for coin in coins:
        coin_id = coin["id"]
        symbol = coin["symbol"].upper()
        name = coin["name"]
        print(f"در حال تحلیل {symbol}...")

        ta_data = get_technical_analysis(coin_id)
        news = get_news_mentions(name, symbol)
        social = get_social_sentiment(symbol)

        score, reasons = score_coin(coin, ta_data, news, social)
        results.append({
            "id": coin_id,
            "symbol": symbol,
            "name": name,
            "score": score,
            "reasons": reasons if reasons else ["بدون سیگنال خاص"],
            "ta_data": ta_data,  # برای محاسبه TP/SL تو build_report لازمه
        })

        time.sleep(REQUEST_DELAY)  # رعایت rate-limit رایگان CoinGecko

    report = build_report(results, performance_section)
    send_telegram_message(report)

    # ذخیره فقط سیگنال‌های نمایش‌داده‌شده همین اجرا برای مقایسه تو اجرای بعدی (۶ ساعت دیگه)
    bullish, bearish = select_top_signals(results)
    save_signals([r for r, _ in bullish], [r for r, _ in bearish])

    print("گزارش ارسال شد.")


if __name__ == "__main__":
    main()
