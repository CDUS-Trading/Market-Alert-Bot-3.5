import requests
import time
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv
import csv
import openai
import pytz

# Load environment variables
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
finnhub_api_key = os.getenv("FINNHUB_API_KEY")
marketaux_api_key = os.getenv("MARKETAUX_API_KEY")

# =============================
# 📋 Utility Functions
# =============================

def classify_headlines_openai_bulk(headlines):
    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You're a financial sentiment classifier. For each headline, respond with just 📈, 📉, or 🔹. Give one per line in the same order."},
                {"role": "user", "content": "\n".join(headlines)}
            ],
            max_tokens=50
        )
        result_text = response.choices[0].message.content.strip()
        sentiments = result_text.splitlines()
        return sentiments
    except Exception as e:
        print("❌ OpenAI classification failed:", e)
        return ["🔹"] * len(headlines)

def is_market_relevant(text):
    keywords = ["fed", "tariff", "rate", "inflation", "yields", "bond", "treasury", "earnings", "revenue", "stocks", "markets", "recession", "jobless", "cpi", "ppi", "gdp", "volatility"]
    return any(k in text.lower() for k in keywords)
# =============================
# 📋 News Scrapers
# =============================

def scrape_headlines(url, selector, base_url=""):
    headlines = []
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, 'html.parser')
        for el in soup.select(selector)[:10]:
            text = el.get_text(strip=True)
            link = el.get("href", "")
            if text and is_market_relevant(text):
                full_link = f"{base_url}{link}" if link.startswith("/") else link
                headlines.append(f"{text} - {full_link}")
    except Exception as e:
        print(f"⚠️ Error scraping {url}:", e)
    return headlines

def get_all_market_news(finnhub_api_key, marketaux_api_key):
    headlines_raw = []

    # 📰 1. Macenews, CNBC, Reuters (Web scraping)
    #headlines_raw += scrape_headlines("https://macenews.com/", ".elementor-heading-title")
    headlines_raw += scrape_headlines("https://www.cnbc.com/world/?region=world", "a.Card-title")
    #headlines_raw += scrape_headlines("https://www.reuters.com/", "a[data-testid='Heading']", base_url="https://www.reuters.com")

    # 📰 2. Finnhub News
    def fetch_finnhub_news():
        url = f"https://finnhub.io/api/v1/news?category=general&token={finnhub_api_key}"
        try:
            response = requests.get(url).json()
            for item in response[:10]:
                title = item.get("headline", "")
                url = item.get("url", "")
                if title:
                    headlines_raw.append(f"{title} - {url}")
        except Exception as e:
            print("❌ Finnhub news fetch failed:", e)

    # 📰 3. Marketaux News
    def fetch_marketaux_news():
        url = f"https://api.marketaux.com/v1/news/all?symbols=SPY&filter_entities=true&language=en&api_token={marketaux_api_key}"
        try:
            response = requests.get(url).json()
            for article in response.get("data", [])[:10]:
                title = article.get("title", "")
                url = article.get("url", "")
                if title:
                    headlines_raw.append(f"{title} - {url}")
        except Exception as e:
            print("❌ Marketaux news fetch failed:", e)

    # Fetch from APIs
    fetch_finnhub_news()
    fetch_marketaux_news()

    # Filter and classify
    headlines_raw = [h for h in headlines_raw if is_market_relevant(h)]
    classified = classify_headlines_openai_bulk(headlines_raw)

    enhanced_news = []
    for original, sentiment in zip(headlines_raw, classified):
        score = {"📈": 3, "📉": -3, "🔹": 0}.get(sentiment, 0)
        enhanced_news.append((sentiment, score, f"{sentiment} {original}"))

    return enhanced_news


# =============================
# 📋 Market Data
# =============================

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

def fetch_finance_data(symbol):
    """Internal helper to get price and prev close from Google Finance"""
    try:
        url = f"https://www.google.com/finance/quote/{symbol}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        
        # Current Price
        price_el = soup.select_one("div.YMlKec.fxKbKc")
        current_price = float(price_el.text.replace(",", "").replace("$", "")) if price_el else None
        
        # Previous Close
        prev_close = None
        for row in soup.select("div.gyFHrc"):
            if "Previous close" in row.text:
                val_text = row.select_one("div.P6K39c").text
                prev_close = float(val_text.replace(",", "").replace("$", ""))
                break
        return current_price, prev_close
    except Exception as e:
        print(f"⚠️ Error fetching {symbol}: {e}")
        return None, None

def get_spx():
    price, _ = fetch_finance_data(".INX:INDEXSP")
    return price if price else "N/A"

def get_vix():
    price, _ = fetch_finance_data("VIX:INDEXCBOE")
    return price if price else "N/A"

def get_es():
    # ES1! is the ticker for E-mini S&P 500 Futures
    price, _ = fetch_finance_data("ESW00:CME_MINI") 
    return price if price else "N/A"

def get_prev_es():
    _, prev_close = fetch_finance_data("ESW00:CME_MINI")
    return prev_close
# =============================
# 📋 Analysis & Bias
# =============================

def estimate_direction(spx, es, prev_es, sentiment_score, vix):
    score = 0
    reasons = []
    if isinstance(es, float) and isinstance(prev_es, float):
        gap = es - prev_es
        reasons.append(f"Calculated ES Gap: {gap:.2f}")
    else:
        gap = 0
        reasons.append("Gap calculation skipped: Missing ES data")


    if gap > 10:
        score += 1
        reasons.append("ES futures lead SPX → bullish")
    elif gap < -10:
        score -= 1
        reasons.append("ES futures lag SPX → bearish")

    if sentiment_score >= 3:
        score += 1
        reasons.append("Positive news bias")
    elif sentiment_score <= -3:
        score -= 1
        reasons.append("Negative news bias")

    if isinstance(vix, float) and vix > 30:
        score -= 1
        reasons.append("High VIX (>30) → bearish weight")

    # 📌 Enforce binary direction — resolve ties toward strongest sentiment
    if score > 0:
        reasons.append("Neutral bias overridden → forced Bullish")
        return "📈 Bullish", reasons
    else:
        reasons.append("Neutral bias overridden → forced Bearish")
        return "📉 Bearish", reasons




# =============================
# 📅 Logging
# =============================

def log_premarket_prediction(date, spx, es, vix, sentiment_score, direction, move_pts):
    log_file = "market_predictions.csv"
    file_exists = os.path.isfile(log_file)

    with open(log_file, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["date", "spx", "es", "vix", "sentiment_score", "predicted_trend", "predicted_move_pts"])
        writer.writerow([date, spx, es, vix, sentiment_score, direction, move_pts])

# =============================
# ⏰ Time Control & Scheduler Helpers
# =============================
def now_chicago():
    tz = pytz.timezone("America/Chicago")
    return datetime.datetime.now(tz)

def wait_until_chicago(target_h, target_m, max_wait_minutes=180):
    """
    Wait until target Chicago time (hour, minute).
    If target time already passed, returns immediately.
    Safety: stop waiting after max_wait_minutes.
    """
    start = now_chicago()
    deadline = start + datetime.timedelta(minutes=max_wait_minutes)

    while True:
        now = now_chicago()
        target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)

        if now >= target:
            return

        if now >= deadline:
            print(f"⚠️ Wait timeout reached. Now={now}, target={target}")
            return

        time.sleep(10)

# =============================
# 📧 Email Notifier
# =============================


def send_email(subject, spx, vix, es, sentiment_score, news, direction, reasons, move_msg, to_email):
    try:
        import pytz
        # Get current time in US/Eastern
        eastern = pytz.timezone('US/Eastern')
        current_time_est = datetime.datetime.now(eastern).strftime('%I:%M %p ET')

        message = MIMEMultipart("alternative")
        message["From"] = os.getenv("EMAIL_USER")
        message["To"] = to_email
        message["Subject"] = subject

        # Plaintext fallback
        body_text = f"""
📊 Pre-Market Test 2 Alert for {datetime.date.today()}
🔹 SPX: {spx}  🔺 VIX: {vix}  📉 ES: {es}
📊 Sentiment Score: {sentiment_score}


📰 Headlines:
{chr(10).join([f"- {h}" for _, _, h in news])}

📊 Market Bias: {direction}
{chr(10).join([f"- {r}" for r in reasons])}

📉 Expected Move: {move_msg}
Generated by CDUS Trading Bot • {current_time_est}
        """

        # HTML version
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
            <h2 style="color: #0d6efd;">📊 Pre-Market Test 2 Alert for {datetime.date.today()}</h2>
            <p>
                <strong>🔹 SPX:</strong> {spx} &nbsp;&nbsp;
                <strong>🔺 VIX:</strong> {vix} &nbsp;&nbsp;
                <strong>📉 ES:</strong> {es}
            </p>

            <p>
                <strong>📊 Sentiment Score:</strong>
                <span style="font-size: 1.2em; font-weight: bold;">{sentiment_score}</span>
            </p>

            <h3>📰 Headlines:</h3>
            <ul>
                {''.join(f"<li>{h}</li>" for _, _, h in news)}
            </ul>

            <h3>📊 Market Bias: {direction}</h3>
                        
            <br>
            
            <p style="font-size: 0.9em; color: #888;">Generated by CDUS Trading Bot • {current_time_est}</p>
        </body>
        </html>
        """

        # Attach both parts
        message.attach(MIMEText(body_text, "plain"))
        message.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(os.getenv("EMAIL_USER"), os.getenv("EMAIL_PASS"))
            server.send_message(message)
            print("✅ Email sent.")
    except Exception as e:
        print("❌ Email failed:", e)

# =============================
# 🚀 Alert Execution Wrapper
# =============================
def run_and_send(slot_label):
    today = datetime.date.today()

    spx = get_spx()
    vix = get_vix()
    es = get_es()
    prev_es = get_prev_es()

    news = get_all_market_news(finnhub_api_key, marketaux_api_key)
    sentiment_score = sum(score for _, score, _ in news)

    direction, reasons = estimate_direction(spx, es, prev_es, sentiment_score, vix)

    log_premarket_prediction(today, spx, es, vix, sentiment_score, direction, move_pts="N/A")

    subject = f"📊 CDUS Git | Pre-Market Alert | {slot_label}"

    send_email(
        subject=subject,
        spx=spx,
        vix=vix,
        es=es,
        sentiment_score=sentiment_score,
        news=news,
        direction=direction,
        reasons=reasons,
        move_msg="N/A",
        to_email=os.getenv("EMAIL_TO")
    )



# =============================
# 📊 Main
# =============================

def main():
    now = now_chicago()
    print("Chicago current time:", now.strftime("%Y-%m-%d %I:%M %p %Z"))

    # Morning run window: before 9:30
    if now.hour < 9 or (now.hour == 9 and now.minute < 30):
        print("⏳ Waiting until 08:10 Chicago time...")
        wait_until_chicago(8, 10, max_wait_minutes=180)
        run_and_send("8:30 Trade")
        return

    # Midday run window: between 9:30 and 1:00 PM
    if now.hour < 13:
        print("⏳ Waiting until 11:15 Chicago time...")
        wait_until_chicago(11, 15, max_wait_minutes=180)
        run_and_send("11:30 Trade")

        print("⏳ Waiting until 11:30 Chicago time...")
        wait_until_chicago(11, 30, max_wait_minutes=90)
        run_and_send("12:00 Trade")
        return

    print("Outside expected schedule window. Exiting.")
   

if __name__ == "__main__":
    main()






