"""
ربات تحلیل‌گر کریپتو (Crypto Signal Bot)
----------------------------------------
هر بار اجرا میشه:
  1) لیست ۵۰ کوین برتر از CoinGecko میگیره
  2) داده کندل (OHLCV) از Binance برای هرکدوم میگیره و تحلیل تکنیکال میزنه
     (RSI, MACD, SMA20/50, حجم نسبت به میانگین)
  3) اخبار اخیر مرتبط با هر کوین رو از RSS های خبری چک می‌کنه
  4) احساسات اجتماعی (Bullish/Bearish) رو از StockTwits میگیره
  5) یه امتیاز نهایی (Score) برای هر کوین حساب می‌کنه
  6) نتیجه رو به صورت پیام تلگرام میفرسته

⚠️ توجه مهم:
این ابزار فقط داده‌ها رو جمع و تحلیل می‌کنه و یک سیستم قانون‌محور (rule-based) ساده‌ست.
هیچ تضمینی برای سودآوری وجود نداره. همیشه با ریسک خودتون معامله کنید و این رو
به‌عنوان مشاوره مالی در نظر نگیرید.
"""

import os
import time
import requests
import pandas as pd
import feedparser
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator

# ---------------- تنظیمات ----------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TOP_N = 50                 # تعداد کوین‌های برتر
KLINE_INTERVAL = "4h"      # تایم‌فریم کندل برای تحلیل تکنیکال
KLINE_LIMIT = 100          # تعداد کندل برای محاسبه اندیکاتورها
REQUEST_TIMEOUT = 15

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


# ---------------- گام ۲: تحلیل تکنیکال از Binance ----------------
def get_technical_analysis(symbol):
    """symbol مثل BTC, ETH ... - سعی می‌کنه جفت‌ارز USDT رو در بایننس پیدا کنه"""
    pair = f"{symbol.upper()}USDT"
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": pair, "interval": KLINE_INTERVAL, "limit": KLINE_LIMIT}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data or len(data) < 30:
            return None

        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)

        rsi = RSIIndicator(df["close"], window=14).rsi().iloc[-1]
        macd_ind = MACD(df["close"])
        macd_line = macd_ind.macd().iloc[-1]
        macd_signal = macd_ind.macd_signal().iloc[-1]
        sma20 = SMAIndicator(df["close"], window=20).sma_indicator().iloc[-1]
        sma50 = SMAIndicator(df["close"], window=min(50, len(df) - 1)).sma_indicator().iloc[-1]

        current_price = df["close"].iloc[-1]
        avg_volume = df["volume"].iloc[-20:].mean()
        last_volume = df["volume"].iloc[-1]
        volume_ratio = (last_volume / avg_volume) if avg_volume > 0 else 1.0

        return {
            "rsi": round(float(rsi), 1),
            "macd_bullish": bool(macd_line > macd_signal),
            "price_above_sma20": bool(current_price > sma20),
            "sma20_above_sma50": bool(sma20 > sma50),
            "volume_ratio": round(float(volume_ratio), 2),
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


def build_report(results):
    results_sorted = sorted(results, key=lambda x: x["score"], reverse=True)
    strong_bullish = [r for r in results_sorted if r["score"] >= 2][:10]
    strong_bearish = [r for r in results_sorted if r["score"] <= -1.5][:5]

    lines = []
    lines.append(f"📊 <b>گزارش تحلیل بازار کریپتو</b>")
    lines.append(f"🕐 {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
    lines.append(f"🔍 تحلیل {len(results)} کوین برتر\n")

    if strong_bullish:
        lines.append("🟢 <b>سیگنال‌های صعودی قوی:</b>")
        for r in strong_bullish:
            lines.append(f"• <b>{r['symbol']}</b> (امتیاز {r['score']}) — {', '.join(r['reasons'][:3])}")
        lines.append("")

    if strong_bearish:
        lines.append("🔴 <b>هشدار سیگنال‌های نزولی:</b>")
        for r in strong_bearish:
            lines.append(f"• <b>{r['symbol']}</b> (امتیاز {r['score']}) — {', '.join(r['reasons'][:3])}")
        lines.append("")

    lines.append("📋 <b>خلاصه کل بازار (مرتب‌شده بر اساس امتیاز):</b>")
    for r in results_sorted:
        lines.append(f"{r['symbol']}: {r['score']}")

    lines.append("\n⚠️ این تحلیل صرفاً جمع‌بندی داده‌هاست و مشاوره مالی نیست.")
    return "\n".join(lines)


def main():
    print("در حال گرفتن لیست کوین‌ها...")
    coins = get_top_coins(TOP_N)
    results = []

    for coin in coins:
        symbol = coin["symbol"].upper()
        name = coin["name"]
        print(f"در حال تحلیل {symbol}...")

        ta_data = get_technical_analysis(symbol)
        news = get_news_mentions(name, symbol)
        social = get_social_sentiment(symbol)

        score, reasons = score_coin(coin, ta_data, news, social)
        results.append({
            "symbol": symbol,
            "name": name,
            "score": score,
            "reasons": reasons if reasons else ["بدون سیگنال خاص"],
        })

        time.sleep(0.3)  # رعایت rate-limit سرویس‌های رایگان

    report = build_report(results)
    send_telegram_message(report)
    print("گزارش ارسال شد.")


if __name__ == "__main__":
    main()
