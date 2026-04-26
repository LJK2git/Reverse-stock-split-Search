import asyncio
import re
from datetime import datetime, timedelta
from typing import List, Tuple, Set
from bs4 import BeautifulSoup
import sys

from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PWTimeout

# ---------- URL builder ----------
def build_url(day_str: str) -> str:
    day = datetime.strptime(day_str, "%Y-%m-%d")
    next_day = (day + timedelta(days=1)).strftime("%Y-%m-%d")
    return (
        f"https://finance.yahoo.com/calendar/splits"
        f"?from={day_str}&to={next_day}&day={day_str}&offset=0&size=100"
    )

# ---------- Scrape single day ----------
async def scrape_day_page(browser: Browser, day_str: str, semaphore: asyncio.Semaphore):
    url = build_url(day_str)
    async with semaphore:
        page: Page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        async def route_intercept(route):
            req = route.request
            if req.resource_type in ("image", "font", "stylesheet"):
                await route.abort()
            elif "google-analytics" in req.url or "analytics" in req.url:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", route_intercept)

        results = []
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            try:
                await page.wait_for_selector("table tbody tr, .simpTblRow, a[data-test='quoteLink']", timeout=7000)
            except PWTimeout:
                pass

            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            ratio_re = re.compile(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)")
            date_re = re.compile(
                r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s*\d{4}",
                re.IGNORECASE
            )

            rows = soup.select("table tbody tr") or soup.select(".simpTblRow")
            seen = set()

            for tr in rows:
                link = tr.find("a", {"data-test": "quoteLink"}) or tr.find("a", href=True)
                if not link:
                    continue

                symbol = link.text.strip().upper()
                href = link.get("href", "")
                full_href = href if href.startswith("http") else "https://finance.yahoo.com" + href

                if not symbol or symbol in seen:
                    continue

                company = ""
                payable_on = ""
                ratio_text = ""

                tds = tr.find_all("td")
                if tds:
                    if len(tds) >= 2:
                        company = tds[1].get_text(separator=" ", strip=True)
                    if len(tds) >= 3:
                        payable_on = tds[2].get_text(separator=" ", strip=True)
                    candidate = tds[-1].get_text(separator=" ", strip=True)
                    m_ratio = ratio_re.search(candidate)
                    if m_ratio:
                        ratio_text = m_ratio.group(0)

                row_text = tr.get_text(separator=" ", strip=True)
                if not payable_on:
                    m_date = date_re.search(row_text)
                    if m_date:
                        payable_on = m_date.group(0)

                if not ratio_text:
                    m_ratio = ratio_re.search(row_text)
                    if m_ratio:
                        ratio_text = m_ratio.group(0)

                is_reverse = False
                m_ratio = ratio_re.search(ratio_text)
                if m_ratio:
                    try:
                        a = float(m_ratio.group(1))
                        b = float(m_ratio.group(2))
                        is_reverse = a > b
                        ratio_text = f"{a:g} - {b:g}"
                    except Exception:
                        pass

                results.append((symbol, full_href, company or "N/A", payable_on or day_str, ratio_text or "N/A", is_reverse))
                seen.add(symbol)

        except Exception:
            pass
        finally:
            try:
                await page.close()
            except Exception:
                pass

        return results

# ---------- Main runner ----------
async def run(date_list: List[str], concurrency: int = 5):
    filter_by_length = True  # ALWAYS YES

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        sem = asyncio.Semaphore(concurrency)
        tasks = [scrape_day_page(browser, d, sem) for d in date_list]
        all_day_results = await asyncio.gather(*tasks)
        await browser.close()

    seen = set()
    reverse_splits = []  # <--- STORED VARIABLE

    for day_res in all_day_results:
        for sym, href, company, payable_on, ratio, is_reverse in day_res:
            if filter_by_length and len(sym) not in (3, 4):
                continue
            if sym in seen:
                continue
            seen.add(sym)

            if is_reverse:
                reverse_splits.append({
                    "ticker": sym,
                    "ratio": ratio,
                    "date": payable_on
                })

    return reverse_splits

# ---------- Auto-run ----------
def main():
    today = datetime.today()

    date_list = [
        today.strftime("%Y-%m-%d"),
        (today + timedelta(days=1)).strftime("%Y-%m-%d"),
        (today + timedelta(days=2)).strftime("%Y-%m-%d"),
        (today + timedelta(days=3)).strftime("%Y-%m-%d"),
        (today + timedelta(days=4)).strftime("%Y-%m-%d"),
    ]

    reverse_splits = asyncio.run(run(date_list, concurrency=5))

    if not reverse_splits:
        print("No reverse splits detected.")
        sys.exit(0)

    # Print ONLY the reverse split entries
    for entry in reverse_splits:
        print(f"{entry['ticker']} — {entry['ratio']} — {entry['date']}")

    # Stored variable for further use
    # reverse_splits = [ {ticker, ratio, date}, ... ]


