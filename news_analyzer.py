import os
import json
import time
import argparse
import warnings
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq
from reliability_utils import (
    cache_read_enveloped,
    cache_write_enveloped,
    stable_hash_text,
)

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
MAX_ANNOUNCEMENTS_FOR_PROMPT = 5
AI_RETRY_BUDGET = 3

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
    loaded, malformed = cache_read_enveloped(
        cache_file,
        expected_source="news_nse_cache",
        default_data=cache
    )
    if malformed:
        print(f"  ⚠ {symbol} NSE cache malformed; rebuilding")
    if isinstance(loaded, dict):
        cache = loaded

    last_fetch = cache.get("last_fetch")
    is_cached  = (last_fetch == today_str)
    new_items  = []

    if not is_cached:
        if last_fetch:
            since = datetime.strptime(last_fetch, "%Y-%m-%d") + timedelta(days=1)
            print(f"  ✓ {symbol} NSE (from {since.strftime('%Y-%m-%d')})")
        else:
            since = datetime.now() - timedelta(days=90)
            print(f"  ✓ {symbol} NSE (first fetch — 90 days)")

        fetched_items = _fetch_announcements_since(symbol, since)
        existing_keys = {(i["date"], i["desc"]) for i in cache["items"]}

        for item in fetched_items:
            key = (item["date"], item["desc"])
            if key not in existing_keys:
                cache["items"].append(item)
                existing_keys.add(key)
                new_items.append(item)

        cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        cache["items"] = [
            i for i in cache["items"]
            if str(i.get("date", ""))[:10] >= cutoff
        ]
        cache["last_fetch"] = today_str

        try:
            cache_write_enveloped(cache_file, "news_nse_cache", cache)
        except Exception:
            pass
    else:
        print(f"  ↺ {symbol} NSE (cached)")

    return cache, new_items


def get_important_announcements(symbol):
    cache_file = NSE_CACHE_DIR / f"{symbol}.json"
    if not cache_file.exists():
        return []

    try:
        cache, malformed = cache_read_enveloped(
            cache_file,
            expected_source="news_nse_cache",
            default_data={"items": []}
        )
        if malformed:
            return []
        items = cache.get("items", [])
        important = [
            f"[{i['date'][:10]}] {i['desc']}"
            for i in sorted(items, key=lambda x: x["date"], reverse=True)
            if is_important(i["desc"])
        ]
        return important[:5]
    except Exception:
        return []


def get_ai_analysis(symbol, max_age_days=30):
    cache_file = AI_CACHE_DIR / f"{symbol}.json"
    if not cache_file.exists():
        return None

    try:
        cached, malformed = cache_read_enveloped(
            cache_file,
            expected_source="news_ai_cache",
            default_data={}
        )
        if malformed or not isinstance(cached, dict):
            return None
        analysis = cached.get("analysis")
        cache_date = cached.get("date")

        if not analysis:
            return None

        if cache_date:
            try:
                saved_date = datetime.strptime(cache_date, "%Y-%m-%d")
                age_days = (datetime.now() - saved_date).days
            except Exception:
                age_days = max_age_days + 1

            if age_days > max_age_days:
                # too old, re-run
                return None

        # keep cached analysis if valid and not stale
        return analysis
    except Exception:
        return None


def save_ai_analysis(symbol, analysis_dict, announcement_hash=None):
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
        cache_write_enveloped(cache_file, "news_ai_cache", {
            "date":     today,
            "analysis": analysis_dict,
            "announcement_hash": announcement_hash
        })
        print(f"    ✓ Cached analysis for {symbol}")
    except Exception as e:
        print(f"    ✗ Failed to cache {symbol}: {e}")


def merge_ai_analysis(existing, incoming):
    if not isinstance(existing, dict):
        return incoming

    if not isinstance(incoming, dict):
        return existing

    merged = existing.copy()

    def normalize(s):
        if not s:
            return ""
        return str(s).strip()

    for field in ["red_flags", "catalysts"]:
        old_val = normalize(existing.get(field, ""))
        new_val = normalize(incoming.get(field, ""))

        if not new_val or new_val.lower() in ["none", "no data available", "analysis unavailable"]:
            merged[field] = old_val or "None"
        elif not old_val or old_val.lower() in ["none", "no data available", "analysis unavailable"]:
            merged[field] = new_val
        elif new_val.lower() == old_val.lower():
            merged[field] = old_val
        else:
            merged[field] = f"{old_val}; {new_val}"

    # Verdict: prefer non-neutral recent signal, else keep existing
    current_verdict = normalize(existing.get("verdict", "Neutral")).title()
    incoming_verdict = normalize(incoming.get("verdict", "Neutral")).title()

    if incoming_verdict in ["Bullish", "Bearish"]:
        merged["verdict"] = incoming_verdict
    else:
        merged["verdict"] = current_verdict or "Neutral"

    return merged


def incremental_ai_analysis(symbol, existing_analysis, new_announcements):
    if not new_announcements:
        return existing_analysis

    incoming = analyze_stock(symbol, new_announcements)
    if not is_analysis_available(incoming):
        return existing_analysis

    return merge_ai_analysis(existing_analysis, incoming)


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


def _build_announcement_block(announcements, max_items=MAX_ANNOUNCEMENTS_FOR_PROMPT):
    if not announcements:
        return "No recent announcements found."

    full_lines = [
        str(ann).strip().replace("\n", " ")
        for ann in announcements[:max_items]
    ]
    return "\n".join(full_lines)


def _build_prompt(symbol, ann_text, compact=False):
    if compact:
        return f"""You are a senior equity analyst for Indian stocks.

Analyze {symbol} using these recent NSE announcements:
{ann_text}

Give concise output in EXACT format:
SYMBOL: {symbol}
RED_FLAGS: [max 2 short points or None]
CATALYSTS: [max 2 short points or None]
VERDICT: Bullish OR Bearish OR Neutral
==="""

    return f"""You are a senior equity analyst covering Indian stock markets.

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


def _analyze_with_prompt(symbol, prompt):
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    raw = response.choices[0].message.content.strip()
    results = parse_response(raw)

    if symbol in results:
        print(f"    ✓ Got response for {symbol}")
        result_data = results[symbol]
        print(f"      red_flags: {result_data.get('red_flags', 'MISSING')[:60]}...")
        print(f"      catalysts: {result_data.get('catalysts', 'MISSING')[:60]}...")
        return results[symbol]

    print(f"    ✗ {symbol} not in parse results")
    if results:
        first_key = list(results.keys())[0]
        print(f"      Using {first_key} data instead")
        return results[first_key]

    return {
        "red_flags": "Analysis unavailable",
        "catalysts": "Analysis unavailable",
        "verdict": "Neutral"
    }


def analyze_stock(symbol, announcements):
    # Quality-first:
    # Try full context first; only split if request hits 413.
    ann_text = _build_announcement_block(announcements, max_items=MAX_ANNOUNCEMENTS_FOR_PROMPT)
    prompt = _build_prompt(symbol, ann_text, compact=False)
    telemetry = {"fallback_mode": "full", "chunks_used": 0}
    for attempt in range(AI_RETRY_BUDGET):
        try:
            result = _analyze_with_prompt(symbol, prompt)
            result["_telemetry"] = telemetry
            return result
        except Exception as e:
            msg = str(e)
            if "429" in msg:
                wait = 20
                print(f"    Rate limit — waiting {wait}s...")
                time.sleep(wait)
                continue
            if "413" in msg:
                print("    Request too large on full prompt — switching to split fallback...")
                break
            print(f"  ✗ {symbol} analysis error: {e}")
            return {
                "red_flags": "Analysis unavailable",
                "catalysts": "Analysis unavailable",
                "verdict": "Neutral",
                "_telemetry": {"fallback_mode": "error", "chunks_used": 0}
            }

    # Split fallback: keep full announcement text per chunk, no pre-truncation.
    if announcements:
        telemetry["fallback_mode"] = "split"
        chunks = [announcements[:3], announcements[3:5]]
        merged = {
            "red_flags": "None",
            "catalysts": "None",
            "verdict": "Neutral"
        }
        got_any = False

        for idx, chunk in enumerate(chunks, start=1):
            if not chunk:
                continue
            ann_text = _build_announcement_block(chunk, max_items=len(chunk))
            prompt = _build_prompt(symbol, ann_text, compact=False)
            try:
                print(f"    Split fallback chunk {idx}/{len(chunks)}...")
                partial = _analyze_with_prompt(symbol, prompt)
                if is_analysis_available(partial):
                    merged = merge_ai_analysis(merged, partial)
                    got_any = True
                    telemetry["chunks_used"] += 1
            except Exception as e:
                msg = str(e)
                if "429" in msg:
                    print("    Rate limit in split fallback — waiting 20s...")
                    time.sleep(20)
                    try:
                        partial = _analyze_with_prompt(symbol, prompt)
                        if is_analysis_available(partial):
                            merged = merge_ai_analysis(merged, partial)
                            got_any = True
                    except Exception:
                        pass
                elif "413" in msg:
                    print(f"    Split chunk {idx} still too large — skipping chunk.")
                else:
                    print(f"    Split chunk {idx} failed: {e}")

        if got_any:
            merged["_telemetry"] = telemetry
            return merged

    return {
        "red_flags": "Analysis unavailable",
        "catalysts": "Analysis unavailable",
        "verdict":   "Neutral",
        "_telemetry": {"fallback_mode": "failed", "chunks_used": 0}
    }


def run_news_analysis(symbols, batch_size=3, screener_session=None):
    print(f"\n=== News Analysis ({len(symbols)} stocks, model: {GROQ_MODEL}) ===\n")

    # step 1 — update NSE cache and gather new items
    print("  Updating NSE announcement cache...")
    symbol_updates = {}
    for symbol in symbols:
        nse_cache, new_items = update_nse_cache(symbol)
        symbol_updates[symbol] = {
            "nse_cache": nse_cache,
            "new_items": new_items,
            "updated": bool(new_items)
        }
        time.sleep(0.3)

    # step 2 — decide what needs analysis
    needs_analysis = []
    for symbol in symbols:
        cached = get_ai_analysis(symbol)
        if cached is None:
            needs_analysis.append(symbol)
        elif symbol_updates[symbol]["updated"]:
            needs_analysis.append(symbol)

    cached_count   = len(symbols) - len(needs_analysis)
    print(f"\n  AI: {cached_count} cached, {len(needs_analysis)} need analysis")

    # step 3 — analyze with web search (new-only if existing cached)
    if needs_analysis:
        print(f"\n  Running Groq compound (web search) for {len(needs_analysis)} stocks...")
        for i, symbol in enumerate(needs_analysis):
            all_announcements = get_important_announcements(symbol)
            announcement_hash = stable_hash_text("||".join(all_announcements))
            cached_for_hash = get_ai_analysis(symbol)
            if (
                cached_for_hash is not None
                and isinstance(cached_for_hash, dict)
            ):
                cached_payload, _ = cache_read_enveloped(
                    AI_CACHE_DIR / f"{symbol}.json",
                    expected_source="news_ai_cache",
                    default_data={}
                )
                if cached_payload.get("announcement_hash") == announcement_hash:
                    print(f"  [{i+1}/{len(needs_analysis)}] {symbol} (unchanged announcements; skip)")
                    continue

            if symbol_updates[symbol]["updated"] and get_ai_analysis(symbol) is not None:
                # incremental run only for new update block
                new_announcements = [
                    f"[{x['date'][:10]}] {x['desc']}"
                    for x in symbol_updates[symbol]["new_items"]
                ]
                print(f"  [{i+1}/{len(needs_analysis)}] {symbol} (incremental {len(new_announcements)} updates)")

                existing_analysis = get_ai_analysis(symbol) or {
                    "red_flags": "No data available",
                    "catalysts": "No data available",
                    "verdict": "Neutral"
                }
                analysis = incremental_ai_analysis(symbol, existing_analysis, new_announcements)

            else:
                announcements = all_announcements
                print(f"  [{i+1}/{len(needs_analysis)}] {symbol} ({len(announcements)} announcements)")
                analysis = analyze_stock(symbol, announcements)

            if is_analysis_available(analysis):
                telemetry = analysis.pop("_telemetry", {})
                if telemetry:
                    print(f"    Telemetry: mode={telemetry.get('fallback_mode')} chunks={telemetry.get('chunks_used')}")
                save_ai_analysis(symbol, analysis, announcement_hash=announcement_hash)
            else:
                # if invalid result, keep old cache if any
                old_cached = get_ai_analysis(symbol)
                if old_cached:
                    analysis = old_cached
                else:
                    analysis = {
                        "red_flags": "No data available",
                        "catalysts": "No data available",
                        "verdict": "Neutral"
                    }
                    # no save for unavailable

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


def load_symbols_from_csv(path):
    if not os.path.exists(path):
        print(f"✗ Symbols CSV not found: {path}")
        return []

    try:
        df = pd.read_csv(path)
    except Exception as e:
        print(f"✗ Could not read symbols CSV {path}: {e}")
        return []

    if "symbol" not in df.columns:
        print(f"✗ CSV missing 'symbol' column: {path}")
        return []

    symbols = (
        df["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )
    symbols = [sym for sym in symbols.tolist() if sym and sym != "NAN"]
    deduped = list(dict.fromkeys(symbols))
    print(f"Loaded {len(deduped)} tracked symbols from {path}")
    return deduped


def main():
    parser = argparse.ArgumentParser(
        description="Run news cache update and AI analysis for tracked symbols."
    )
    parser.add_argument(
        "--symbols-csv",
        default="output/tracked_symbols.csv",
        help="CSV with a 'symbol' column used to determine tracked symbols.",
    )
    args = parser.parse_args()

    symbols = load_symbols_from_csv(args.symbols_csv)
    if not symbols:
        raise SystemExit(1)

    run_news_analysis(symbols)


if __name__ == "__main__":
    main()
