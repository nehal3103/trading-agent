"""
guidance_tracker.py

Tracks management guidance across quarters for each stock.
Stores up to 4 quarters of guidance history.
Cross-checks previous guidance against current results.
Computes a management credibility score.
"""

import os
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client             = Groq(api_key=os.getenv("GROQ_API_KEY"))
GUIDANCE_CACHE_DIR = Path("output/guidance_cache")
GUIDANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

GROQ_MODEL   = "meta-llama/llama-4-scout-17b-16e-instruct"
MAX_QUARTERS = 4


def load_guidance_cache(symbol):
    cache_file = GUIDANCE_CACHE_DIR / f"{symbol}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            pass
    return {
        "symbol":            symbol,
        "quarters":          [],
        "credibility_score": 0,
        "credibility_label": "Unrated"
    }


def save_guidance_cache(symbol, cache):
    cache_file = GUIDANCE_CACHE_DIR / f"{symbol}.json"
    try:
        cache_file.write_text(json.dumps(cache, indent=2))
    except Exception as e:
        print(f"  ⚠ Could not save guidance cache for {symbol}: {e}")


def extract_guidance(symbol, concall_text, ppt_text, quarter_label, period):
    combined = ""
    if concall_text:
        combined += f"--- CONCALL SUMMARY ---\n{concall_text}\n\n"
    if ppt_text:
        combined += f"--- INVESTOR PRESENTATION ---\n{ppt_text}\n\n"

    if not combined.strip():
        return None

    prompt = f"""You are extracting structured forward-looking guidance from corporate documents for {symbol} ({period}).

Extract ONLY specific, quantified, forward-looking statements — things management has PROMISED or GUIDED for the future.
Do NOT include historical results — only future targets and commitments.

Extract the following:

1. REVENUE_GUIDANCE: Specific revenue or growth targets (e.g. "FY26 revenue target of INR X cr")
2. MARGIN_GUIDANCE: Specific margin targets or floors (e.g. "EBITDA margin floor of 22%")
3. ORDER_BOOK_GUIDANCE: Order book or inflow targets (e.g. "order inflow target of INR X cr for FY26")
4. CAPACITY_GUIDANCE: Capacity additions or expansions (e.g. "new plant by Q2FY27")
5. OTHER_GUIDANCE: Any other specific forward commitments
6. PPT_HIGHLIGHT_1 through PPT_HIGHLIGHT_5: 3-5 most important points from investor presentation

Rules:
- Use EXACT numbers and quotes where available
- If no specific guidance for a category, write "Not guided"
- One clear sentence per category maximum
- PPT highlights should be short punchy bullet points

Format EXACTLY like this — no extra text:

REVENUE_GUIDANCE: [text or "Not guided"]
MARGIN_GUIDANCE: [text or "Not guided"]
ORDER_BOOK_GUIDANCE: [text or "Not guided"]
CAPACITY_GUIDANCE: [text or "Not guided"]
OTHER_GUIDANCE: [text or "Not guided"]
PPT_HIGHLIGHT_1: [text]
PPT_HIGHLIGHT_2: [text]
PPT_HIGHLIGHT_3: [text]
PPT_HIGHLIGHT_4: [text or ""]
PPT_HIGHLIGHT_5: [text or ""]

Documents:
{combined[:6000]}"""

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        raw = response.choices[0].message.content.strip()

        result = {
            "guidance": {
                "revenue":    "Not guided",
                "margins":    "Not guided",
                "order_book": "Not guided",
                "capacity":   "Not guided",
                "other":      "Not guided"
            },
            "ppt_highlights": []
        }

        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("REVENUE_GUIDANCE:"):
                result["guidance"]["revenue"] = line.split(":", 1)[1].strip()
            elif line.startswith("MARGIN_GUIDANCE:"):
                result["guidance"]["margins"] = line.split(":", 1)[1].strip()
            elif line.startswith("ORDER_BOOK_GUIDANCE:"):
                result["guidance"]["order_book"] = line.split(":", 1)[1].strip()
            elif line.startswith("CAPACITY_GUIDANCE:"):
                result["guidance"]["capacity"] = line.split(":", 1)[1].strip()
            elif line.startswith("OTHER_GUIDANCE:"):
                result["guidance"]["other"] = line.split(":", 1)[1].strip()
            elif line.startswith("PPT_HIGHLIGHT_"):
                text = line.split(":", 1)[1].strip()
                if text and text not in ('""', ""):
                    result["ppt_highlights"].append(text)

        return result

    except Exception as e:
        print(f"  ✗ Guidance extraction error for {symbol}: {e}")
        return None


def cross_check_guidance(symbol, previous_quarter, current_concall_text):
    prev_guidance = previous_quarter.get("guidance", {})
    prev_period   = previous_quarter.get("period", "previous quarter")

    guided_items = []
    for key, val in prev_guidance.items():
        if val and val != "Not guided":
            guided_items.append(f"{key}: {val}")

    if not guided_items or not current_concall_text:
        return "unclear", "Insufficient data to cross-check"

    guided_text = "\n".join(guided_items)

    prompt = f"""You are cross-checking management guidance for {symbol}.

PREVIOUS GUIDANCE (from {prev_period} concall):
{guided_text}

CURRENT QUARTER RESULTS (from latest concall):
{current_concall_text[:3000]}

Did management deliver on their previous guidance?

Format EXACTLY like this:

DELIVERED: yes OR partial OR no OR unclear
NOTES: [2-3 specific sentences comparing guided vs actual, with numbers]"""

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        raw = response.choices[0].message.content.strip()

        delivered = "unclear"
        notes     = "Cross-check incomplete"

        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("DELIVERED:"):
                delivered = line.split(":", 1)[1].strip().lower()
                if delivered not in ("yes", "partial", "no", "unclear"):
                    delivered = "unclear"
            elif line.startswith("NOTES:"):
                notes = line.split(":", 1)[1].strip()

        return delivered, notes

    except Exception as e:
        print(f"  ✗ Cross-check error for {symbol}: {e}")
        return "unclear", "Cross-check unavailable"


def compute_credibility_score(quarters):
    score = 0
    for q in quarters:
        delivered = q.get("delivered")
        if delivered == "yes":
            score += 1
        elif delivered == "no":
            score -= 1

    if score >= 3:
        label = "High — consistently delivers"
    elif score >= 1:
        label = "Good — mostly delivers"
    elif score == 0:
        label = "Neutral — mixed or unrated"
    elif score >= -1:
        label = "Caution — partial delivery"
    else:
        label = "Poor — frequently misses guidance"

    return score, label


def update_guidance(symbol, concall_text, ppt_text,
                    concall_id, quarter_label, period):
    cache = load_guidance_cache(symbol)
    today = datetime.now().strftime("%Y-%m-%d")

    existing_ids   = [q.get("concall_id") for q in cache["quarters"]]
    is_new_quarter = concall_id not in existing_ids

    if is_new_quarter and (concall_text or ppt_text):
        print(f"  ✓ {symbol} — new quarter ({period}), extracting guidance...")

        extracted = extract_guidance(
            symbol, concall_text, ppt_text, quarter_label, period
        )

        if extracted:
            new_quarter = {
                "quarter":          quarter_label,
                "period":           period,
                "concall_id":       concall_id,
                "guidance":         extracted["guidance"],
                "ppt_highlights":   extracted["ppt_highlights"],
                "actuals":          None,
                "delivered":        None,
                "credibility_note": None,
                "extracted_date":   today
            }

            # cross-check previous quarter if available
            if cache["quarters"]:
                prev = cache["quarters"][0]
                if concall_text and prev.get("guidance"):
                    print(f"    Cross-checking vs {prev['period']} guidance...")
                    delivered, notes = cross_check_guidance(
                        symbol, prev, concall_text
                    )
                    prev["delivered"]        = delivered
                    prev["credibility_note"] = notes
                    prev["actuals"]          = f"Checked against {period} results"

            cache["quarters"].insert(0, new_quarter)
            cache["quarters"] = cache["quarters"][:MAX_QUARTERS]

            score, label = compute_credibility_score(cache["quarters"])
            cache["credibility_score"] = score
            cache["credibility_label"] = label

            save_guidance_cache(symbol, cache)
    else:
        if not is_new_quarter:
            print(f"  ↺ {symbol} — guidance already extracted for {period}")
        else:
            print(f"  ✗ {symbol} — no document data available")

    return build_notion_output(cache)


def build_notion_output(cache):
    quarters = cache.get("quarters", [])

    management_guidance = "No guidance data available"
    ppt_highlights      = "No presentation data available"
    guidance_track      = "No track record yet"

    if quarters:
        latest = quarters[0]
        g      = latest.get("guidance", {})

        guidance_lines = [f"Quarter: {latest.get('period', 'Unknown')}", ""]
        for key, label in [
            ("revenue",    "Revenue Target"),
            ("margins",    "Margin Guidance"),
            ("order_book", "Order Book Target"),
            ("capacity",   "Capacity Plans"),
            ("other",      "Other Commitments")
        ]:
            val = g.get(key, "Not guided")
            if val and val != "Not guided":
                guidance_lines.append(f"{label}: {val}")

        management_guidance = "\n".join(guidance_lines)

        highlights = latest.get("ppt_highlights", [])
        if highlights:
            ppt_highlights = "\n".join(
                f"• {h}" for h in highlights if h
            )

    score = cache.get("credibility_score", 0)
    label = cache.get("credibility_label", "Unrated")

    track_lines = [f"Credibility Score: {score:+d}/4 — {label}", ""]

    for q in quarters:
        delivered = q.get("delivered")
        icon = {"yes": "✓", "no": "✗", "partial": "~"}.get(delivered, "?")
        period = q.get("period", "Unknown")
        note   = q.get("credibility_note", "Pending next quarter check")
        track_lines.append(f"{icon} {period}: {note}")

    guidance_track = "\n".join(track_lines)

    return {
        "management_guidance": management_guidance[:2000],
        "ppt_highlights":      ppt_highlights[:2000],
        "guidance_track":      guidance_track[:2000],
        "credibility_score":   score,
        "credibility_label":   label
    }


def get_guidance_output(symbol):
    cache = load_guidance_cache(symbol)
    return build_notion_output(cache)


if __name__ == "__main__":
    from pathlib import Path
    import json as _json

    CONCALL_CACHE = Path("output/concall_cache")

    for symbol in ["QPOWER", "CGPOWER", "COALINDIA"]:
        cache_file = CONCALL_CACHE / f"{symbol}.json"
        if not cache_file.exists():
            print(f"{symbol}: no concall cache found")
            continue

        data     = _json.loads(cache_file.read_text())
        concalls = data.get("concalls", data.get("quarters", []))
        ppts     = data.get("ppts", [])

        if not concalls:
            print(f"{symbol}: no concall data")
            continue

        latest       = concalls[0]
        concall_text = latest.get("text", "")
        concall_id   = latest.get("id", "unknown")
        ppt_text     = ppts[0].get("text", "") if ppts else ""

        print(f"\n{'='*40}")
        print(f"Testing {symbol}...")
        result = update_guidance(
            symbol, concall_text, ppt_text,
            concall_id, "Q3FY26", latest.get("fetched_date", "Unknown")
        )

        print(f"\nManagement Guidance:\n{result['management_guidance']}")
        print(f"\nPPT Highlights:\n{result['ppt_highlights']}")
        print(f"\nTrack Record:\n{result['guidance_track']}")