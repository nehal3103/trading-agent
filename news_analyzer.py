import os
import json
import time
import warnings
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

logging.getLogger("pdfminer").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

load_dotenv()

client         = Groq(api_key=os.getenv("GROQ_API_KEY"))
NSE_COOKIE_DIR = Path("output/nse_cookies")
NSE_CACHE_DIR  = Path("output/nse_cache")
AI_CACHE_DIR   = Path("output/ai_cache")

for d in [NSE_COOKIE_DIR, NSE_CACHE_DIR, AI_CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

GROQ_MODEL = "groq/compound"

IMPORTANT_TYPES = {
    "order", "contract", "bagging", "award", "win",
    "appointment", "resignation", "cessation", "director",
    "result", "quarterly", "annual", "dividend",
    "acquisition", "merger", "expansion", "capacity",
    "press release", "analyst", "investor"
}


def is_important(desc):
    return any(kw in desc.lower() for kw in IMPORTANT_TYPES)


def _fetch_announcements_since(symbol, since_date):
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

        items = []
        for item in data:
            date    = str(item.get("an_dt", ""))
            desc    = str(item.get("desc", "")).strip()
            subject = str(item.get("subject", "")).strip()
            if not desc:
                continue
            display = subject if len(subject) > len(desc) else desc
            items.append({"date": date, "desc": display})

        return items

    except ImportError:
        return []
    except Exception as e:
        print(f"  ⚠ {symbol} NSE fetch error: {str(e)[:80]}")
        return []


def update_nse_cache(symbol):
    cache_file = NSE_CACHE_DIR / f"{symbol}.json"
    today_str  = datetime.now().strftime("%Y-%m-%d")

    cache = {"last_fetch": None, "items": []}
    if cache_file.exists():
        try:
            loaded = json.loads(cache_file.read_text())
            if "items" in loaded:
                cache = loaded
            elif "text" in loaded:
                cache = {"last_fetch": None, "items": []}
        except Exception:
            pass

    last_fetch = cache.get("last_fetch")
    is_cached  = (last_fetch == today_str)

    if not is_cached:
        if last_fetch:
            since = datetime.strptime(last_fetch, "%Y-%m-%d") + timedelta(days=1)
            print(f"  ✓ {symbol} NSE (from {since.strftime('%Y-%m-%d')})")
        else:
            since = datetime.now() - timedelta(days=90)
            print(f"  ✓ {symbol} NSE (first fetch — 90 days)")

        new_items     = _fetch_announcements_since(symbol, since)
        existing_keys = {(i["date"], i["desc"]) for i in cache["items"]}

        for item in new_items:
            key = (item["date"], item["desc"])
            if key not in existing_keys:
                cache["items"].append(item)
                existing_keys.add(key)

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
        print(f"  ↺ {symbol} NSE (cached)")

    return cache


def get_important_announcements(symbol):
    cache_file = NSE_CACHE_DIR / f"{symbol}.json"
    if not cache_file.exists():
        return []

    try:
        cache = json.loads(cache_file.read_text())
        items = cache.get("items", [])
        important = [
            f"[{i['date'][:10]}] {i['desc']}"
            for i in sorted(items, key=lambda x: x["date"], reverse=True)
            if is_important(i["desc"])
        ]
        return important[:5]
    except Exception:
        return []


def get_ai_analysis(symbol):
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
    # only cache real results — not failures or errors
    bad_indicators = [
        "Analysis unavailable",
        "No data available", 
        "Error",
        "Failed",
        "Token expired",
        "API error"
    ]
    
    red_flags = analysis_dict.get("red_flags", "").lower()
    catalysts = analysis_dict.get("catalysts", "").lower()
    
    # Don't save if any field indicates an error
    if any(indicator.lower() in red_flags for indicator in bad_indicators) or \
       any(indicator.lower() in catalysts for indicator in bad_indicators):
        print(f"    ⚠ Not caching {symbol} — error detected in analysis")
        return
        
    # Don't save if all fields are empty/generic
    if (red_flags in ["none", ""] and 
        catalysts in ["none", ""] and 
        analysis_dict.get("verdict", "").lower() == "neutral"):
        print(f"    ⚠ Not caching {symbol} — empty analysis")
        return
    
    cache_file = AI_CACHE_DIR / f"{symbol}.json"
    today      = datetime.now().strftime("%Y-%m-%d")
    try:
        cache_file.write_text(json.dumps({
            "date":     today,
            "analysis": analysis_dict
        }))
        print(f"    ✓ Cached analysis for {symbol}")
    except Exception as e:
        print(f"    ✗ Failed to cache {symbol}: {e}")


def is_analysis_available(analysis_dict):
    """Check if analysis contains real data vs error messages"""
    if not analysis_dict or not isinstance(analysis_dict, dict):
        return False
        
    bad_indicators = [
        "Analysis unavailable",
        "No data available", 
        "Error",
        "Failed",
        "Token expired",
        "API error"
    ]
    
    red_flags = analysis_dict.get("red_flags", "").lower()
    catalysts = analysis_dict.get("catalysts", "").lower()
    
    # Check if any field indicates an error
    if any(indicator.lower() in red_flags for indicator in bad_indicators) or \
       any(indicator.lower() in catalysts for indicator in bad_indicators):
        return False
        
    # Check if we have at least some meaningful content
    return not (red_flags in ["none", ""] and 
                catalysts in ["none", ""] and 
                analysis_dict.get("verdict", "").lower() == "neutral")


def parse_response(raw):
    results       = {}
    current_sym   = None
    current       = {"red_flags": "None", "catalysts": "None", "verdict": "Neutral"}
    current_field = None
    field_lines   = []

    def flush():
        nonlocal current_field, field_lines
        if current_field and field_lines:
            cleaned = [
                l.lstrip("-•* ").strip().rstrip(".")
                for l in field_lines
                if l.strip() and l.strip() not in ("-", "•", "*")
            ]
            cleaned = [l for l in cleaned if l]
            if not cleaned or (len(cleaned) == 1 and cleaned[0].lower() == "none"):
                current[current_field] = "None"
            else:
                current[current_field] = ". ".join(cleaned) + "."
        current_field = None
        field_lines   = []

    for line in raw.split("\n"):
        s = line.strip()
        if s.startswith("**") and s.endswith("**"):
            continue
        if s.startswith("SYMBOL:"):
            flush()
            if current_sym:
                results[current_sym] = current.copy()
            current_sym   = s.replace("SYMBOL:", "").strip().strip("*").strip()
            current       = {"red_flags": "None", "catalysts": "None", "verdict": "Neutral"}
            current_field = None
            field_lines   = []
        elif s.startswith("RED_FLAGS:"):
            flush()
            current_field = "red_flags"
            field_lines   = [s.split(":", 1)[1].strip()]
        elif s.startswith("CATALYSTS:"):
            flush()
            current_field = "catalysts"
            field_lines   = [s.split(":", 1)[1].strip()]
        elif s.startswith("VERDICT:"):
            flush()
            raw_verdict        = s.split(":", 1)[1].strip()
            current["verdict"] = raw_verdict.split()[0].strip() if raw_verdict else "Neutral"
        elif s == "===":
            flush()
            if current_sym:
                results[current_sym] = current.copy()
            current_sym   = None
            current       = {"red_flags": "None", "catalysts": "None", "verdict": "Neutral"}
        elif current_field and s:
            field_lines.append(s)

    flush()
    if current_sym:
        results[current_sym] = current.copy()

    return results


def analyze_stock(symbol, announcements):
    if not announcements:
        ann_text = "No recent announcements found."
    else:
        ann_text = "\n".join(announcements)

    prompt = f"""You are a senior equity analyst covering Indian stock markets.

I need you to research and analyze {symbol} (NSE listed Indian company).

Recent NSE announcements for {symbol}:
{ann_text}

Use web search to find:
1. Latest news about {symbol} — recent orders, results, management changes, expansions
2. What analysts are saying about this company
3. Any concerns or red flags mentioned in recent news
4. Management guidance or future targets if available

Then provide your analysis in EXACTLY this format — plain text, no markdown:

SYMBOL: {symbol}
RED_FLAGS: [specific risks with names/numbers — or None]
CATALYSTS: [specific growth drivers with exact figures — or None]
VERDICT: Bullish OR Bearish OR Neutral
==="""

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            raw     = response.choices[0].message.content.strip()
            results = parse_response(raw)
            
            # DEBUG
            if symbol in results:
                print(f"    ✓ Got response for {symbol}")
                result_data = results[symbol]
                print(f"      red_flags: {result_data.get('red_flags', 'MISSING')[:60]}...")
                print(f"      catalysts: {result_data.get('catalysts', 'MISSING')[:60]}...")
            else:
                print(f"    ✗ {symbol} not in parse results")
                if results:
                    first_key = list(results.keys())[0]
                    results[symbol] = results[first_key]
                    print(f"      Using {first_key} data instead")

            return results.get(symbol, {
                "red_flags": "Analysis unavailable",
                "catalysts": "Analysis unavailable",
                "verdict":   "Neutral"
            })

        except Exception as e:
            if "429" in str(e) and attempt < 2:
                wait = 30 * (attempt + 1)  # 30s then 60s
                print(f"    Rate limit — waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  ✗ {symbol} analysis error: {e}")
                return {
                    "red_flags": "Analysis unavailable",
                    "catalysts": "Analysis unavailable",
                    "verdict":   "Neutral"
                }

    return {
        "red_flags": "Analysis unavailable",
        "catalysts": "Analysis unavailable",
        "verdict":   "Neutral"
    }


def run_news_analysis(symbols, batch_size=3, screener_session=None):
    print(f"\n=== News Analysis ({len(symbols)} stocks, model: {GROQ_MODEL}) ===\n")

    # step 1 — update NSE cache
    print("  Updating NSE announcement cache...")
    for symbol in symbols:
        update_nse_cache(symbol)
        time.sleep(0.3)

    # step 2 — determine what needs analysis
    needs_analysis = [s for s in symbols if get_ai_analysis(s) is None]
    cached_count   = len(symbols) - len(needs_analysis)
    print(f"\n  AI: {cached_count} cached, {len(needs_analysis)} need analysis")

    # step 3 — analyze with web search
    if needs_analysis:
        print(f"\n  Running Groq compound (web search) for {len(needs_analysis)} stocks...")
        for i, symbol in enumerate(needs_analysis):
            announcements = get_important_announcements(symbol)
            print(f"  [{i+1}/{len(needs_analysis)}] {symbol} ({len(announcements)} announcements)")
            analysis = analyze_stock(symbol, announcements)
            save_ai_analysis(symbol, analysis)
            time.sleep(5)  # increased buffer to avoid TPM limits
    else:
        print("  All analysis loaded from cache")

    # step 4 — compile results
    final_results = {}
    for symbol in symbols:
        analysis = get_ai_analysis(symbol)
        if analysis and is_analysis_available(analysis):
            final_results[symbol] = analysis
        else:
            final_results[symbol] = {
                "red_flags": "No data available",
                "catalysts": "No data available",
                "verdict":   "Neutral"
            }

    print(f"\n  ✓ Analysis ready for {len(final_results)} stocks")
    return final_results


if __name__ == "__main__":
    test    = ["QPOWER", "CGPOWER", "COALINDIA"]
    results = run_news_analysis(test)

    for sym, analysis in results.items():
        print(f"\n{'='*40}")
        print(f"{sym}:")
        print(f"  Red Flags : {analysis['red_flags']}")
        print(f"  Catalysts : {analysis['catalysts']}")
        print(f"  Verdict   : {analysis['verdict']}")