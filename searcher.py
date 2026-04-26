import os
import re
import sys
import json
import discord
import asyncio
import requests
import feedparser
import yfinance as yf
from yahoo import run #requires the yahoo.py file
from openai import OpenAI
from bs4 import BeautifulSoup
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from googlenewsdecoder import gnewsdecoder



#track the actual non dev logs with the log function
#all of the stuff that is with print("...") is all for debugging )
# ----------------------- CONFIG ----------------------- #


with open("secrets.json", "r", encoding="utf-8") as f:
    secrets = json.load(f)


TOKEN = secrets["TOKEN"]
YOUR_CHANNEL_ID = secrets["CHANNEL_ID"]
LOG_CHANNEL_ID = secrets["LOG_CHANNEL_ID"]
OPENAI_API_KEY = secrets["OPENAI_API_KEY"]
client = OpenAI(api_key=OPENAI_API_KEY)

#Will spam you with logs, good for seeing if bot is flipping up
SPAM = False
AIMODE = True
KEYWORDS_FILE = "keywords.txt"
#Secondary keywords will post splits as 50/50
SECONDARY_KEYWORDS_FILE = "secondary_keywords.txt"
PROCESSED_FILE = "processed.txt"
FEEDS_FILE = "feeds.txt"
BLOCKED_PHRASES = ["lieu"]
POST_LIEU = ["round", "rounded"]
NASDAQ_FEED_DOMAIN = "nasdaqtrader.com"
user_req = False
HEADERS = {"User-Agent": "DiscordBot Coolperson@gmail.com"}

# Load RSS feed URLs
RSS_FEED_URLS = []
if os.path.exists(FEEDS_FILE):
    with open(FEEDS_FILE, "r", encoding="utf-8") as f:
        RSS_FEED_URLS = [line.strip() for line in f if line.strip()]

# ----------------------- BOT SETUP ----------------------- #

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ----------------------- GLOBALS ----------------------- #

reported_tickers = set()
reverse_split_reported = set()
#DO NOT TOUCH YAHOO MODE
yahoo_mode = False

# ----------------------- UTILITIES ----------------------- #

async def safe_send(channel, message):
    for _ in range(3):
        try:
            await channel.send(message)
            return
        except discord.errors.DiscordServerError:
            await asyncio.sleep(2)
        except discord.errors.HTTPException:
            await asyncio.sleep(2)

#price chekaa
def ticker_price(symbol):
    try:
        ticker = yf.Ticker(symbol)

        # Most reliable method: get latest close price
        data = ticker.history(period="1d")

        if data is None or data.empty:
            if SPAM:
                log(f"[ERROR] No price data returned for symbol: {symbol}")
            return None

        price = data["Close"].iloc[-1]

        if price is None:
            if SPAM:
                log(f"[ERROR] Close price missing for symbol: {symbol}")
            return None

        return float(price)

    except Exception as e:
        if SPAM:
            log(f"[EXCEPTION] Failed to fetch price for {symbol}: {e}")
        return None


def google_link(input_value):
    interval_time = None  # interval is optional, default is None

    source_url = input_value

    try:
        decoded_url = gnewsdecoder(source_url, interval=interval_time)

        if decoded_url.get("status"):
            real_url = decoded_url["decoded_url"]
            print("Decoded URL:", real_url)
            return real_url
        else:
            log("Error:", decoded_url["message"])
            return None
    except Exception as e:
        log(f"Google error occurred: {e}")
        return None

#so i can make and host my own llm in future with data trained from ts and now helps with other shit
def save_to_dataset(url, label, date=None, ticker=None):
    file = "dataset.csv"

    # prevent duplicates
    existing_urls = set()
    if os.path.exists(file):
        with open(file, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(",")
                if parts:
                    existing_urls.add(parts[0])

    if url in existing_urls:
        return  # already saved

    # write row
    with open(file, "a", encoding="utf-8") as f:
        if os.stat(file).st_size == 0:
            f.write("url,label,date,ticker\n")  # header

        f.write(f"{url},{label},{date or ''},{ticker or ''}\n")

def load_dataset_labels():
    dataset = {}
    file = "dataset.csv"

    if not os.path.exists(file):
        return dataset

    with open(file, "r", encoding="utf-8") as f:
        next(f, None)  # skip header
        for line in f:

            parts = line.strip().split(",")
            # Must have at least: url,label,date,ticker
            if len(parts) < 4:
                continue

            url = parts[0]
            label_raw = parts[1]
            ticker = parts[-1].strip().upper()  # last column is always ticker
            #skips the broken tickers
            if not re.fullmatch(r"[A-Z]{1,5}", ticker):
                continue
            try:#make sure ticker isnt cooked
                label = int(label_raw)
            except ValueError:
                continue
            if ticker:
                dataset[ticker] = label

    return dataset

#yoohooo
def ticker_in_dataset(ticker):
    dataset = load_dataset_labels()
    return ticker.upper() in dataset

def save_reverse_split_to_dataset(ticker, date):
    file = "dataset.csv"

    # Load existing tickers
    dataset = load_dataset_labels()
    if ticker.upper() in dataset:
        return  # already saved

    # Append new entry
    with open(file, "a", encoding="utf-8") as f:
        if os.stat(file).st_size == 0:
            f.write("url,label,date,ticker\n")
        f.write(f"Yahoo,1,{date},{ticker.upper()}\n")


def log(message: str):
    print(message)
    if bot.is_ready() and LOG_CHANNEL:
        asyncio.create_task(safe_send(LOG_CHANNEL, f"{message[:1900]}"))


def load_file_lines(filename: str):
    if not os.path.exists(filename):
        return []
    with open(filename, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

#loading
keywords = load_file_lines(KEYWORDS_FILE)
secondary_keywords = load_file_lines(SECONDARY_KEYWORDS_FILE)



def save_file_lines(filename: str, lines):
    with open(filename, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(f"{line}\n")

def fetch_article_text_sync(url: str):
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.extract()
        return soup.get_text(separator=" ")
    except requests.RequestException:
        return None

async def fetch_article_text(url: str):
    return await asyncio.to_thread(fetch_article_text_sync, url)

def extract_tickers(text: str):
    tickers = set()

    # --- PRIORITY 1: NASDAQ / NYSE ---
    primary_pattern = re.findall(
        r"(?:NASDAQ|NYSE)\s*(?:[:\-\(]\s*|\s+)([A-Z]{1,5})\b",
        text,
        re.IGNORECASE
    )

    if primary_pattern:
        tickers.update(t.upper() for t in primary_pattern)
        return list(tickers)

    # --- PRIORITY 2: CSE / OTC tiers ---
    secondary_pattern = re.findall(
        r"(?:CSE|OTC|OTCQB|OTCQX|OTCMKTS|PINK)\s*(?:[:\-\(]\s*|\s+)([A-Z]{1,5})\b",
        text,
        re.IGNORECASE
    )

    tickers.update(t.upper() for t in secondary_pattern)

    fallback = re.findall(r"\(([A-Z]{1,5})\)", text)

    blacklist = {
        "NASDAQ", "NYSE", "TSXV", "TSX", "AMEX", "CSE", "OTC",
        "OTCQB", "OTCQX", "OTCMKTS", "PINK", "Frankfurt", "Nasdaq"
    }

    for f in fallback:
        if f not in blacklist:
            tickers.add(f.upper())

    return list(tickers)

def ticker_to_cik_sync(ticker: str):
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        data = requests.get(url, headers=HEADERS).json()
        for item in data.values():
            if item["ticker"].lower() == ticker.lower():
                return str(item["cik_str"]).zfill(10)
    except Exception:
        return None
    return None

async def ticker_to_cik(ticker: str):
    return await asyncio.to_thread(ticker_to_cik_sync, ticker)


def get_latest_filing_sync(cik: str):
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        data = requests.get(url, headers=HEADERS).json()
        filing = data["filings"]["recent"]
        accession = filing["accessionNumber"][0].replace("-", "")
        doc = filing["primaryDocument"][0]
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{doc}"
    except Exception:
        return None

async def get_latest_filing(cik: str):
    return await asyncio.to_thread(get_latest_filing_sync, cik)

def fetch_text_sync(url):
    try:
        return requests.get(url, headers=HEADERS, timeout=10).text
    except Exception:
        return None

async def fetch_text(url):
    return await asyncio.to_thread(fetch_text_sync, url)

#SEC search
async def check_sec_for_fractional(ticker: str):
    channel = MAIN_CHANNEL
    ticker_upper = ticker.upper()
    global reported_tickers
    cik = await ticker_to_cik(ticker)
    if user_req:
        print("User request has been maaaaddddddddddddddddeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")
    if not cik:
        log(f"CIK not found for ticker {ticker_upper}")
        reported_tickers.add(ticker_upper)
        return False, None

    filing_url = await get_latest_filing(cik)
    if not filing_url:
        log(f"Filing URL not found for ticker {ticker_upper}")
        reported_tickers.add(ticker_upper)
        return False, None

    filing_text = await fetch_text(filing_url)
    if not filing_text:
        log(f"Filing text missing for ticker {ticker_upper} (URL: {filing_url})")
        reported_tickers.add(ticker_upper)
        return False, filing_url

    if re.search(r"\bfractional\b|\bfraction\b", filing_text, re.IGNORECASE):
        if any(re.search(rf"\b{p}\b", filing_text, re.IGNORECASE) for p in BLOCKED_PHRASES):
            if any(re.search(rf"\b{p}\b", filing_text, re.IGNORECASE) for p in POST_LIEU):
                if AIMODE == True:
                    ai_result = await openai_search(filing_url)
                    if ai_result["should_post"] == 1:
                        if not yahoo_mode:
                            await safe_send(channel,f"**Results for:**\n{filing_url}\n{ai_result['text']}\n**Rounds up in LIEU**")
                else:
                    if not yahoo_mode:
                        await safe_send(channel, f"**Results for ticker:**\n{filing_url}\n**Ticker:** {ticker_upper}\n**Rounds up in LIEU**")
                reported_tickers.add(ticker_upper)
            else:
                log(f"Larper SEC filing detected: {filing_url}")
            return True, filing_url
        else:
            if AIMODE == True:
                ai_result = await openai_search(filing_url)
                if ai_result["should_post"] == 1:
                    if not yahoo_mode:
                        await safe_send(channel,f"**Results for:**\n{filing_url}\n{ai_result['text']}")
            else:
                if not yahoo_mode:
                    await safe_send(channel, f"**Results for:**\n{filing_url}\n**Ticker:** {ticker_upper}")
            reported_tickers.add(ticker_upper)
            return True, filing_url
    else:
        if SPAM == True:
            log(f"No keywords found in SEC filing for ticker {ticker_upper} (URL: {filing_url})")
        return False, filing_url

#for yoohoo no ai cuz we already have all needed information
async def sec_check_no_ai(ticker):
    global AIMODE, yahoo_mode
    old_mode = AIMODE
    AIMODE = False
    yahoo_mode = True
    found, link = await check_sec_for_fractional(ticker)
    yahoo_mode = False
    AIMODE = old_mode
    return found, link


async def process_reverse_splits():
    global reverse_split_reported

    today = datetime.today()
    date_list = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]

    reverse_splits = await run(date_list, concurrency=5)

    new_splits = []  # store new tickers for SEC lookup

    for entry in reverse_splits:
        ticker = entry["ticker"].upper()

        if ticker in reverse_split_reported:
            continue

        reverse_split_reported.add(ticker)
        new_splits.append(entry)

    return new_splits


async def get_sec_link_only(ticker):
    cik = await ticker_to_cik(ticker)
    if not cik:
        return None

    filing_url = await get_latest_filing(cik)
    return filing_url

async def handle_reverse_split_alerts():
    new_splits = await process_reverse_splits()

    for entry in new_splits:
        ticker = entry["ticker"].upper()
        ratio = entry["ratio"]
        date = entry["date"]

        # Skip if already in dataset
        if ticker_in_dataset(ticker):
            continue

        # SEC check (no AI)
        found, sec_link = await sec_check_no_ai(ticker)

        # Price lookup
        price = ticker_price(ticker)
        price_text = f"{price}" if price is not None else "NONE"

        # If SEC found fractional keywords → include SEC link
        if found and sec_link:
            msg = (
                f"**Results for:**\n"
                f"{sec_link}\n"
                f"TICKER: {ticker}\n"
                f"DATE: {date}\n"
                f"PRICE: {price_text}"
            )

        else:
            # No fractional keywords → 50/50
            msg = (
                f"**Results for:**\n"
                f"Yahoo Calendar Search\n"
                f"TICKER: {ticker}\n"
                f"DATE: {date}\n"
                f"PRICE: {price_text}\n"
                f"**50/50**"
            )

        save_reverse_split_to_dataset(ticker, date)
        await safe_send(MAIN_CHANNEL, msg)


def search_keywords(text: str, keywords: list):
    results = {}
    text_lower = text.lower()

    for kw in keywords:
        pattern = re.compile(rf"\b{re.escape(kw.lower())}\b")
        matches = list(pattern.finditer(text_lower))

        snippets = []
        for m in matches[:3]:
            start = max(m.start() - 75, 0)
            end = min(m.end() + 50, len(text))
            snippet = text[start:end].strip()
            snippet_highlighted = re.sub(
                re.escape(kw),
                f"**{kw}**",
                snippet,
                flags=re.IGNORECASE
            )
            snippets.append(snippet_highlighted)

        results[kw] = {"count": len(matches), "snippets": snippets}

    return results

#Normal Search
async def check_article_for_roundup(item):
    channel = MAIN_CHANNEL



    # ---------------- FETCH TEXT (same logic as AI, no cleaning) ---------------- #
    # decode Google News redirect URLs first
    if re.match(r"https?://news.google.com", item):
        item = google_link(item)
    if item and re.match(r"https?://finance\.yahoo\.com", item):
        log(f"[BLOCKED] Yahoo Finance link blocked: {item}")
        return False
    if item is None:
        log("Google Decode failed")
        return False
    text = await fetch_article_text(item)

    # Fallback if missing or too short
    if not text or len(text) < 500:
        if SPAM:
            log(f"[ROUNDUP] Primary fetch failed or too short, using fallback fetch")
        def fallback_fetch():
            try:
                headers = {
                    "User-Agent": "MyReverseSplitScanner/1.0 (Coolperson@gmail.com)"
                }
                resp = requests.get(item, headers=headers, timeout=10)
                resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.extract()

                return soup.get_text()
            except:
                return None

        text = await asyncio.to_thread(fallback_fetch)

    # If still no text → give up early
    if not text:
        log(f"[ROUNDUP] ERROR: Both fetch methods failed for URL: {item}")
        processed.add(item)
        return False

    # ---------------- RAW TEXT (no cleaning) ---------------- #
    # text stays exactly as fetched

    # ---------------- TICKER EXTRACTION ---------------- #
    tickers = extract_tickers(text)
    tickers_str = ", ".join(tickers) if tickers else "No tickers found"

    # ---------------- BLOCKED PHRASE LOGIC ---------------- #
    if contains_blocked_phrase(text):
        if contains_post_lieu_phrase(text):
            if AIMODE:
                ai_result = await openai_search(item)
                if ai_result["should_post"] == 1:
                    await safe_send(channel,f"**Results for:**\n{item}\n{ai_result['text']}\n**Rounds up in LIEU**")
            else:
                await safe_send(channel,
                    f"**Results for:**\n{item}\n**Tickers:** {tickers_str}\n**Rounds up in LIEU**")
        else:
            if SPAM:
                log(f"[ROUNDUP] Blocked phrase detected: {item}")
            processed.add(item)
        return True

    # ---------------- KEYWORD SEARCH ---------------- #
    results = search_keywords(text, keywords)
    primary = any(data["count"] > 0 for data in results.values())

    secondary_hit = False
    fifty_fifty = False

    if not primary:
        secondary_results = search_keywords(text, secondary_keywords)
        secondary_hit = any(data["count"] > 0 for data in secondary_results.values())
        if secondary_hit:
            fifty_fifty = True
    else:
        secondary_hit = False

    if not primary and not secondary_hit:
        log(f"[ROUNDUP] No keywords found: {item}")
        return False


    # ---------------- AI FINAL CHECK ---------------- #
    if AIMODE:
        ai_result = await openai_search(item)
        if ai_result["should_post"] == 1:
            if fifty_fifty:
                await safe_send(channel, f"**Results for:**\n{item}\n{ai_result['text']}\n**50/50**")
            else:
                await safe_send(channel, f"**Results for:**\n{item}\n{ai_result['text']}")
    else:
        if fifty_fifty:
            await safe_send(channel,f"**Results for:**\n{item}\n**Tickers:** {tickers_str}\n**50/50**")
        else:
            await safe_send(channel,f"**Results for:**\n{item}\n**Tickers:** {tickers_str}")

    return True

def contains_blocked_phrase(text: str):
    text_lower = text.lower()
    return any(
        re.search(rf"\b{re.escape(p.lower())}\b", text_lower)
        for p in BLOCKED_PHRASES
    )

def contains_post_lieu_phrase(text):
    text_lower = text.lower()
    return any(
        re.search(rf"\b{re.escape(p.lower())}\b", text_lower)
        for p in POST_LIEU
    )

def fetch_rss_feed(feed_url: str):
    return feedparser.parse(feed_url).entries

async def second_check(item, reported_tickers):
    text = await fetch_article_text(item)
    if text:
        tickers = extract_tickers(text)
        if not tickers:
            log(f"[SECOND CHECK] No tickers found in: {item}")
            return
        for ticker in tickers:
            ticker_upper = ticker.upper()
            #print("TICKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
            if ticker_upper not in reported_tickers:
                await check_sec_for_fractional(ticker_upper)
                reported_tickers.add(ticker_upper)


#Ai search+ handles urls differently because its needed i thnk
async def openai_search(input_value: str):
    try:
        if re.match(r"https?://news.google.com", input_value):
            decoded = google_link(input_value)
            if decoded:
                input_value = decoded  # added


        if re.match(r"https?://", input_value):
            base_url = input_value.split("?")[0]  # added
        else:
            base_url = input_value  # added

        # added: Load dataset once
        dataset_labels = load_dataset_labels()

        # added: URL-level dedupe
        if os.path.exists("dataset.csv"):
            with open("dataset.csv", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith(base_url + ","):
                        if SPAM:
                            log(f"[AI] URL already processed: {base_url}")
                        return {"text": "Already processed", "should_post": 0}

        # ---------------- FETCH TEXT ---------------- #
        text = None
        if SPAM:
            log(f"[AI] Starting AI search for: {input_value}")
        if re.match(r"https?://", input_value):
            # Try bot fetch first
            if SPAM:
                log(f"[AI] Fetching article text for URL")
            #google linkz
            if re.match(r"https?://news.google.com", input_value):
                input_value = google_link(input_value)
            if input_value and re.match(r"https?://finance\.yahoo\.com", input_value):
                log(f"[AI BLOCKED] Yahoo Finance link blocked: {input_value}")
                return {"text": "Yahoo Finance links are blocked.", "should_post": 0}


            text = await fetch_article_text(input_value)

            # Fallback to your working method
            if not text or len(text) < 500:
                if SPAM:
                    log(f"[AI] Primary fetch failed or too short, using fallback fetch")
                def fallback_fetch():
                    try:
                        headers = {
                            "User-Agent": "MyReverseSplitScanner/1.0 (your_email@example.com)"
                        }
                        resp = requests.get(input_value, headers=headers, timeout=10)
                        resp.raise_for_status()

                        soup = BeautifulSoup(resp.text, "html.parser")

                        for tag in soup(["script", "style"]):
                            tag.extract()

                        return soup.get_text()
                    except:
                        return None

                text = await asyncio.to_thread(fallback_fetch)

            if not text:
                log(f"[AI] ERROR: Both fetch methods failed for URL: {input_value}")
                return {"text": "Failed to fetch article (both methods failed)", "should_post": 0 }

        else:
            if SPAM:
                log(f"[AI] Treating input as raw text")
            text = input_value

        # ---------------- CLEAN TEXT ---------------- #
        try:
            lines = text.splitlines()
            cleaned_lines = [line.strip() for line in lines if line.strip()]
            cleaned_text = "\n".join(cleaned_lines)
            #have this in the range of 15-8k
            limited_text = cleaned_text[:10000]
        except Exception as e:
            log(f"[AI] ERROR cleaning text: {e}")
            return {"text": "AI text cleaning failed.", "should_post": 0}
        # ---------------- PROMPT ---------------- #
        prompt = (
            """
You must ONLY extract a CONFIRMED and DEFINITIVE effective date of a reverse stock split or share consolidation.

STRICT RULES:
- The date MUST be explicitly stated as the effective date.
- The date MUST be confirmed (not estimated or conditional).

DO NOT RETURN a date if it is:
- expected
- anticipated
- planned
- estimated
- targeted
- "by" a certain date
- "on or about"
- "or such other time"
- conditional on approvals
- forward-looking

If the date is not 100% confirmed and fixed, treat it as NONE.

IGNORE ALL unrelated dates (resignations, meetings, filings, etc).

OUTPUT FORMAT ONLY:

TICKER: (THETICKER)
DATE: (DATE or NONE)
"""
        )

        final_input = prompt + "\n\n" + limited_text

        # ---------------- OPENAI CALL ---------------- #
        try:
            ai_response = await asyncio.to_thread(
                client.responses.create,
                model="gpt-4o-mini",
                input=final_input,
                max_output_tokens=100)
        except Exception as e:
            log(f"[AI] ERROR during OpenAI request: {e}")
            return {"text": "AI request failed (API call).", "should_post": 0}
        try:
            if SPAM:
                log("[AI] parsing AI output")
            output_text = ai_response.output[0].content[0].text
        except Exception as e:
            log(f"[AI] ERROR parsing AI output: {e}")
            return {"text": "AI output parsing failed.", "should_post": 0}

        # ---------------- PARSE AI OUTPUT ---------------- #
        date = None
        ticker = None
        label = 0
        should_post=0
        dataset_labels = load_dataset_labels()

        try:
            date_match = re.search(r"DATE:\s*(.+)", output_text, re.IGNORECASE)
            if date_match:
                extracted = date_match.group(1).strip()
                if extracted.lower() in ["none","no","n/a","null",""]:
                    extracted = None
                if extracted:
                    date = extracted
                    label = 1

            ticker_match = re.search(r"TICKER:\s*(.+)", output_text, re.IGNORECASE)
            if ticker_match:
                extracted_ticker = ticker_match.group(1).strip()
                if extracted_ticker.lower() in ["none","no","n/a","null",""]:
                    extracted_ticker = None
                # Needed because of weird ai slop
                if extracted_ticker and not re.fullmatch(r"[A-Za-z]{1,5}", extracted_ticker):
                    log(f"[AI] Invalid ticker returned by model: {extracted_ticker}")
                    extracted_ticker = None
                ticker = extracted_ticker
            else:
                ticker = None
                log("[AI] Ticker couldnt be found in ai response")
            #should we post it?
            #print(output_text)
            if ticker:
                ticker_upper = ticker.upper()
                if ticker_upper in dataset_labels:
                    old_label = dataset_labels[ticker_upper]
                    #if already processed
                    if old_label == 1:
                        log(f"[AI] skipping {ticker_upper}. Effective date has already been posted with a date")
                    #already processed but new still has no date
                    if old_label == 0 and label == 0:
                        log(f"[AI] skipping {ticker_upper}. Still no effective date found")
                    #already processed but new one has a date
                    if old_label == 0 and label == 1:
                        should_post = 1
                else:
                    #new tickas
                    if label == 1:
                        should_post = 1
                    if label == 0:
                        log(f"[AI]{ticker_upper} Has no date.For article {input_value}")
            # if no ticker has been found
            else:
                if label == 1:
                    should_post = 1
                else:
                    should_post = 0
                    log(f"[AI]NONE Has no date.For article {input_value}")
            #price loookuppp
            price = None
            if should_post and ticker:
                price = ticker_price(ticker)
                if price is not None:
                    output_text += f"\nPRICE: {price}"
                else:
                    output_text += "\nPRICE: NONE"
        except Exception as e:
            log(f"[AI] ERROR parsing extracted fields: {e}")

        # ---------------- SAVE TO DATASET ---------------- #
        if ticker:
            if re.match(r"https?://", input_value):
                save_to_dataset(input_value, label, date, ticker)
            else:
                log(f"Data set failed to save")
        else:
            if re.match(r"https?://", input_value):
                save_to_dataset(input_value, label, date, ticker)

            #log(f"Data set save failed most likely because ticker is {ticker}")

        # ---------------- COST TRACKING ---------------- #
        usage = ai_response.usage
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens

        INPUT_COST_PER_1K = 0.00015
        OUTPUT_COST_PER_1K = 0.0006

        total_cost = (input_tokens / 1000 * INPUT_COST_PER_1K) + \
                     (output_tokens / 1000 * OUTPUT_COST_PER_1K)

        cost_file = "costs.txt"

        try:
            if os.path.exists(cost_file):
                with open(cost_file, "r") as f:
                    previous_total = float(f.read().strip() or 0)
            else:
                previous_total = 0.0
        except:
            previous_total = 0.0

        new_total = previous_total + total_cost

        with open(cost_file, "w") as f:
            f.write(str(new_total))

        log(f"AI Cost: ${total_cost:.6f} | Total: ${new_total:.6f}")

        return {
            "text": output_text,
            "should_post": should_post
                }
    except Exception as e:
        log(f"OpenAI Error: {e}")
        return {"text": "AI request failed (internal error).", "should_post": 0}


# ----------------------- PROCESSING ----------------------- #

async def process_rss_feed():
    global reported_tickers

    # Re-load keywords in case they changed via commands
    current_keywords = load_file_lines(KEYWORDS_FILE)
    processed = set(load_file_lines(PROCESSED_FILE))
    # We will still return this list if you want the rss_feed_task to send
    # specific summaries, but check_article_for_roundup handles its own sending now.
    articles_to_send = []

    for feed_url in RSS_FEED_URLS:
        entries = await asyncio.to_thread(fetch_rss_feed, feed_url)

        for entry in entries:
            item = entry.link

            if item in processed:
                continue

            # --- CASE 1: NASDAQ Feed (Direct SEC search) ---
            if NASDAQ_FEED_DOMAIN in feed_url:
                # 1. Find everything inside parentheses
                parentheses_matches = re.findall(r"\(([^)]+)\)", entry.title)

                for match in parentheses_matches:
                    # 2. Skip matches that are clearly status updates
                    if any(word in match.upper() for word in ["UPDATE", "CLOSED", "CORRECTED", "NEW"]):
                        continue

                    # 3. Split by comma first (to handle multiple different stocks like (AAPL, MSFT))
                    potential_blocks = [b for a in match.split(",") for b in a.split("&")]

                    for block in potential_blocks:
                        # 4. Take only the part BEFORE the first slash
                        # This turns "BACQ/R/U" into "BACQ" and "AAPL" stays "AAPL"
                        ticker_upper = block.split("/")[0].strip().upper()

                        if ticker_upper and len(ticker_upper) <= 10:
                            if SPAM == True:
                                log(f"Found: {item}\nFor ticker: {ticker_upper}")

                            # 5. Check SEC only if we haven't reported this specific ticker yet
                            if ticker_upper not in reported_tickers:
                                await check_sec_for_fractional(ticker_upper)
                                #print(f"LINKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKK {item}")
                        else:
                            # This catches empty strings or weirdly long text that escaped the filter
                            if ticker_upper:
                                log(f"Found: {item}\nProblem with ticker: {ticker_upper}")
                                processed.add(item)
                            else:
                                log(f"No ticker has been found for: {item}")
                                processed.add(item)

                processed.add(item)
                continue

            # --- CASE 2: NORMAL ARTICLE ---
            # Using your existing function as requested
            keyword_found = await check_article_for_roundup(item)

            # check sec fillings
            if re.match(r"https?://news.google.com", item):
                decoded = google_link(item)
                if decoded:
                    #print(f"THIS LINKKKKKKKKKKKKKKKKKKKK: {item}")
                    #saves the un processed link bc its useless to saved processed one
                    processed.add(item)
                    item = decoded
            if not keyword_found:
                await second_check(item, reported_tickers)
                processed.add(item)

            processed.add(item)
            # only needed if code is extreamly fucked
            #log(f"Processed article: {item}")

    # Save the updated processed list to file
    save_file_lines(PROCESSED_FILE, list(processed))

    return articles_to_send

# ----------------------- TASKS & EVENTS ----------------------- #

@tasks.loop(minutes=15)
async def rss_feed_task():
    articles = await process_rss_feed()
    print("Searching for articles")

    # NEW: handle reverse splits
    await handle_reverse_split_alerts()

    if articles:
        for article in articles:
            await safe_send(MAIN_CHANNEL, article)
        log("Bot finished going through articles")

@bot.event
async def on_ready():
    global LOG_CHANNEL, MAIN_CHANNEL
    LOG_CHANNEL = bot.get_channel(LOG_CHANNEL_ID)
    MAIN_CHANNEL = bot.get_channel(YOUR_CHANNEL_ID)

    log("Bot connected")

    if not rss_feed_task.is_running():
        rss_feed_task.start()


# ----------------------- COMMANDS ----------------------- #

@bot.command(name="commands")
async def commands_list(ctx):
    await safe_send(ctx.channel, "!search\n!addkeyword\n!removekeyword\n!listkeyword\n!testplaywright\n!restart")


@bot.command()
async def testplaywright(ctx):
    """Tests whether Playwright can launch Chromium."""
    await ctx.send("Testing Playwright…")

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto("https://example.com")
            title = await page.title()
            await browser.close()

        await ctx.send(f"Playwright is working\n Page title: **{title}**")

    except Exception as e:
        error_text = str(e)

        # Discord max length is 4000 chars
        if len(error_text) > 3500:
            error_text = error_text[:3500] + "\n...[truncated]"

        await ctx.send(f"Playwright FAILED:\n```\n{error_text}\n```")


@bot.command(name="search")
async def search(ctx, *, inputs):
    user_req = True
    inputs_list = [i.strip() for i in inputs.split(",") if i.strip()]
    keywords = load_file_lines(KEYWORDS_FILE)
    print(f"User Search Has started for {inputs_list}")
    for item in inputs_list:
        if re.match(r"https?://", item):
            #print("this ran")
            keyword_found = await check_article_for_roundup(item)
            # check sec fillings
            if not keyword_found:
                await second_check(item, reported_tickers)
        else:
            #print("this shit ran")
            ticker = item.upper()
            cik = await ticker_to_cik(ticker)
            await check_sec_for_fractional(ticker)

@bot.command(name="price")
async def price_command(ctx, symbol: str):
    symbol = symbol.upper()

    # Fetch price using your helper
    price = ticker_price(symbol)

    if price is None:
        # Log error if SPAM mode is on
        if SPAM:
            log(f"[PRICE] Failed to fetch price for {symbol}")

        await safe_send(ctx.channel, f"Could not fetch price for **{symbol}**.")
        return

    # Success
    await safe_send(ctx.channel, f"**{symbol}** price: `{price}`")


@bot.command(name="costs")
async def cat_price(ctx):
    filename = "costs.txt"

    try:
        if not os.path.exists(filename):
            await safe_send(ctx.channel, f"File not found: **{filename}**")
            return

        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()

        if not content:
            await safe_send(ctx.channel, f"**{filename}** is empty.")
            return

        # Truncate to keep Discord message under limits
        MAX_LEN = 1900
        out = content if len(content) <= MAX_LEN else content[:MAX_LEN] + "\n\n[truncated]"

        await safe_send(ctx.channel, f"**{filename}**\n```text\n{out}\n```")

    except Exception as e:
        log(f"Error reading {filename}: {e}")
        await safe_send(ctx.channel, f"Error reading **{filename}**: {e}")


@bot.command(name="addkeyword")
async def add_keyword(ctx, *, new_keywords):
    keywords = load_file_lines(KEYWORDS_FILE)
    new_list = [k.strip() for k in new_keywords.split(",") if k.strip()]
    added = [k for k in new_list if k.lower() not in (kw.lower() for kw in keywords)]

    if added:
        keywords.extend(added)
        save_file_lines(KEYWORDS_FILE, keywords)
        await safe_send(ctx.channel, f"Added keywords: {', '.join(added)}")
    else:
        await safe_send(ctx.channel, "No new keywords added (already exist).")

@bot.command(name="removekeyword")
async def remove_keyword(ctx, *, to_remove):
    keywords = load_file_lines(KEYWORDS_FILE)
    remove_list = [k.strip().lower() for k in to_remove.split(",") if k.strip()]
    filtered = [k for k in keywords if k.lower() not in remove_list]
    removed = [k for k in keywords if k.lower() in remove_list]

    save_file_lines(KEYWORDS_FILE, filtered)

    if removed:
        await safe_send(ctx.channel, f"Removed keywords: {', '.join(removed)}")
    else:
        await safe_send(ctx.channel, "No keywords matched.")

@bot.command(name="listkeyword")
async def list_keywords(ctx):
    keywords = load_file_lines(KEYWORDS_FILE)
    if keywords:
        await safe_send(ctx.channel, "Current keywords:\n" + "\n".join(keywords))
    else:
        await safe_send(ctx.channel, "No keywords found.")


@bot.command(name="restart")
async def restart(ctx):
    await safe_send(ctx.channel, "Restarting bot...")
    python = sys.executable
    os.execl(python, python, *sys.argv)

# ----------------------- RUN BOT ----------------------- #

bot.run(TOKEN)
