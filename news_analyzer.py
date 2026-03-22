import os
import re
import json
import time
import math
import warnings
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from google import genai

logging.getLogger("pdfminer").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

load_dotenv()

client         = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
NSE_COOKIE_DIR = Path("output/nse_cookies")
NSE_CACHE_DIR  = Path("output/nse_cache")
AI_CACHE_DIR   = Path("output/ai_cache")

for d in [NSE_COOKIE_DIR, NSE_CACHE_DIR, AI_CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

GEMINI_MODEL = "gemini-2.5-flash"

IMPORTANT_TYPES = {
    "order", "contract", "bagging", "award", "win",
    "appointment", "resignation", "cessation", "director",
    "result", "quarterly", "annual", "dividend",
    "acquisition", "merger", "expansion", "capacity",
    "press release", "analyst", "investor"
}


def is_important(desc):
    return any(kw in desc.lower() for kw in IMPORTANT_TYPES)


def fetch_pdf_text(pdf_url, session, max_chars=2000):
    try:
        import pdfplumber
        import io
        response = session.get(pdf_url, timeout=10)
        if response.status_code != 200:
            return ""
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            text = ""
            for page in pdf.pages[:3]:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except ImportError:
        return ""
    except Exception:
        return ""


def _fetch_announcements_since(symbol, since_date):
    """
    Fetches announcements from since_date to today.
    Used for incremental updates — only fetches new data.
    """
    try:
        from nse import NSE
        with NSE(download_folder=str(NSE_COOKIE_DIR)) as nse:
            data = nse.announcements(
                symbol=symbol,
                from_date=since_date,
                to_date=datetime.now()
            )

        if not data:
            return []

        pdf_session = requests.Session()
        pdf_session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Referer":    "https://www.nseindia.com"
        })
        pdf_session.get("https://www.nseindia.com", timeout=20)

        items = []
        for item in data:
            date    = str(item.get("an_dt", ""))
            desc    = str(item.get("desc", "")).strip()
            subject = str(item.get("subject", "")).strip()
            pdf_url = item.get("attchmntFile", "")

            if not desc:
                continue

            display  = subject if len(subject) > len(desc) else desc
            pdf_text = ""

            if is_important(desc) and pdf_url:
                full_url = (
                    f"https://nsearchives.nseindia.com/{pdf_url}"
                    if not pdf_url.startswith("http") else pdf_url
                )
                pdf_text = fetch_pdf_text(full_url, pdf_session)

            items.append({
                "date":    date,
                "desc":    display,
                "details": pdf_text
            })

        return items

    except ImportError:
        return []
    except Exception as e:
        print(f"  ⚠ {symbol} fetch error: {str(e)[:80]}")
        return []


def get_announcements(symbol):
    """
    Returns announcements text using incremental caching.

    Cache stores all historical announcements fetched so far.
    On each call only fetches new announcements since last fetch.
    This means after the first run, only 1 day of data is fetched
    instead of 90 days every time.
    """
    cache_file = NSE_CACHE_DIR / f"{symbol}.json"
    today_str  = datetime.now().strftime("%Y-%m-%d")

    # load existing cache — handle both old and new cache formats
    cache = {"last_fetch": None, "items": []}
    if cache_file.exists():
        try:
            loaded = json.loads(cache_file.read_text())
            # old format had "date" and "text" keys
            # new format has "last_fetch" and "items" keys
            if "items" in loaded:
                cache = loaded
            elif "text" in loaded:
                # migrate old format — discard old text, will refetch
                cache = {"last_fetch": None, "items": []}
        except Exception:
            pass

    last_fetch = cache.get("last_fetch")
    is_cached  = (last_fetch == today_str)

    if not is_cached:
        # determine fetch window
        if last_fetch:
            # incremental — only fetch since last run
            since = datetime.strptime(last_fetch, "%Y-%m-%d") + timedelta(days=1)
            print(f"  ✓ {symbol} (incremental from {since.strftime('%Y-%m-%d')})")
        else:
            # first time — fetch last 90 days
            since = datetime.now() - timedelta(days=90)
            print(f"  ✓ {symbol} (first fetch — 90 days)")

        new_items = _fetch_announcements_since(symbol, since)

        # merge new items with existing, remove duplicates by date+desc
        existing_keys = {(i["date"], i["desc"]) for i in cache["items"]}
        for item in new_items:
            key = (item["date"], item["desc"])
            if key not in existing_keys:
                cache["items"].append(item)
                existing_keys.add(key)

        # keep only last 90 days of items
        cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        cache["items"] = [
            i for i in cache["items"]
            if str(i.get("date", ""))[:10] >= cutoff
        ]

        cache["last_fetch"] = today_str

        try:
            cache_file.write_text(json.dumps(cache))
        except Exception:
            pass
    else:
        print(f"  ↺ {symbol} (NSE cached)")

    # format items as readable text
    if not cache["items"]:
        return f"{symbol}: No announcements in last 90 days", is_cached

    lines = [f"Corporate announcements for {symbol}:"]
    for item in sorted(cache["items"], key=lambda x: x["date"], reverse=True)[:15]:
        line = f"\n  [{item['date']}] {item['desc']}"
        if item.get("details"):
            line += f"\n    DETAILS: {item['details']}"
        lines.append(line)

    return "\n".join(lines), is_cached


def get_ai_analysis(symbol):
    """Returns cached AI analysis for today if available."""
    cache_file = AI_CACHE_DIR / f"{symbol}.json"
    today      = datetime.now().strftime("%Y-%m-%d")
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if cached.get("date") == today:
                return cached["analysis"]
        except Exception:
            pass
    return None


def save_ai_analysis(symbol, analysis_dict):
    """Saves AI analysis to daily cache."""
    cache_file = AI_CACHE_DIR / f"{symbol}.json"
    today      = datetime.now().strftime("%Y-%m-%d")
    try:
        cache_file.write_text(json.dumps({
            "date":     today,
            "analysis": analysis_dict
        }))
    except Exception:
        pass


def analyze_batch(batch_data):
    if not batch_data:
        return {}

    combined = ""
    for item in batch_data:
        combined += f"\nSTOCK: {item['symbol']}\n{item['announcements']}\n===\n"

    prompt = f"""You are analyzing NSE corporate announcements for Indian stocks.
For EACH stock provide specific, factual analysis based on the actual announcement content.

Rules:
- For order wins: mention the ORDER VALUE and CLIENT if available
- For appointments: mention WHO was appointed and their background if given
- For results: mention actual revenue/profit numbers if available
- Do NOT say "typically" or "often" — only state what the data actually says
- If specific details are missing, say so explicitly
- Be concise but specific — 2-3 lines per section

Format EXACTLY like this for each stock — no extra text:

SYMBOL: [symbol]
RED_FLAGS: [specific issues with details, or None]
CATALYSTS: [specific positives with order value/client/numbers, or None]
VERDICT: Bullish OR Bearish OR Neutral
===

Data:
{combined}"""

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
        raw = response.text.strip()

        results        = {}
        current_symbol = None
        current        = {
            "red_flags": "None",
            "catalysts": "None",
            "verdict":   "Neutral"
        }

        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("SYMBOL:"):
                if current_symbol:
                    results[current_symbol] = current.copy()
                current_symbol = line.replace("SYMBOL:", "").strip()
                current        = {
                    "red_flags": "None",
                    "catalysts": "None",
                    "verdict":   "Neutral"
                }
            elif line.startswith("RED_FLAGS:"):
                current["red_flags"] = line.split(":", 1)[1].strip()
            elif line.startswith("CATALYSTS:"):
                current["catalysts"] = line.split(":", 1)[1].strip()
            elif line.startswith("VERDICT:"):
                current["verdict"] = line.split(":", 1)[1].strip()
            elif line == "===":
                if current_symbol:
                    results[current_symbol] = current.copy()
                current_symbol = None
                current        = {
                    "red_flags": "None",
                    "catalysts": "None",
                    "verdict":   "Neutral"
                }

        if current_symbol:
            results[current_symbol] = current.copy()

        return results

    except Exception as e:
        print(f"  ✗ Batch error: {e}")
        return {
            item["symbol"]: {
                "red_flags": "Analysis unavailable",
                "catalysts": "Analysis unavailable",
                "verdict":   "Neutral"
            }
            for item in batch_data
        }


def run_news_analysis(symbols, batch_size=5):
    print(f"\n=== News Analysis ({len(symbols)} stocks, model: {GEMINI_MODEL}) ===\n")

    print("  Fetching NSE announcements (incremental)...")
    all_data          = []
    needs_ai_analysis = []

    for symbol in symbols:
        text, is_cached = get_announcements(symbol)
        all_data.append({"symbol": symbol, "announcements": text})

        if not is_cached:
            time.sleep(1)

        if get_ai_analysis(symbol) is None:
            needs_ai_analysis.append(symbol)

    ai_cached = len(symbols) - len(needs_ai_analysis)
    print(f"\n  AI: {ai_cached} cached, {len(needs_ai_analysis)} need analysis")

    if needs_ai_analysis:
        print(f"\n  Running Gemini for {len(needs_ai_analysis)} stocks...")
        batch_data  = [d for d in all_data if d["symbol"] in needs_ai_analysis]
        num_batches = math.ceil(len(batch_data) / batch_size)

        for i in range(0, len(batch_data), batch_size):
            batch     = batch_data[i : i + batch_size]
            batch_num = math.ceil((i + 1) / batch_size)
            syms      = [b["symbol"] for b in batch]
            print(f"  Batch {batch_num}/{num_batches}: {syms}")

            batch_results = analyze_batch(batch)
            for sym, analysis in batch_results.items():
                save_ai_analysis(sym, analysis)

            time.sleep(2)
    else:
        print("  All AI analysis loaded from cache — Gemini not called")

    final_results = {}
    for symbol in symbols:
        analysis = get_ai_analysis(symbol)
        if analysis:
            final_results[symbol] = analysis
        else:
            final_results[symbol] = {
                "red_flags": "Analysis unavailable",
                "catalysts": "Analysis unavailable",
                "verdict":   "Neutral"
            }

    print(f"\n  ✓ Analysis ready for {len(final_results)} stocks")
    return final_results


if __name__ == "__main__":
    test    = ["CGPOWER", "QPOWER", "COALINDIA"]
    results = run_news_analysis(test, batch_size=3)
    for sym, analysis in results.items():
        print(f"\n{'='*40}")
        print(f"{sym}:")
        print(f"  Red Flags : {analysis['red_flags']}")
        print(f"  Catalysts : {analysis['catalysts']}")
        print(f"  Verdict   : {analysis['verdict']}")