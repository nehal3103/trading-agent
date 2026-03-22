import os
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN       = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

notion = Client(auth=NOTION_TOKEN)


def safe_float(val, decimals=2):
    try:
        return round(float(val), decimals)
    except:
        return None


def safe_str(val):
    if pd.isna(val):
        return ""
    return str(val).strip()


def parse_ai_narrative(narrative):
    import json
    import ast

    if narrative is None or (isinstance(narrative, float) and pd.isna(narrative)):
        return "None", "None", "Neutral"

    if isinstance(narrative, dict):
        return (
            str(narrative.get("red_flags", "None")).strip() or "None",
            str(narrative.get("catalysts", "None")).strip() or "None",
            str(narrative.get("verdict", "Neutral")).strip() or "Neutral"
        )

    text = str(narrative).strip()

    # Try JSON parsing
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return (
                str(data.get("red_flags", "None")).strip() or "None",
                str(data.get("catalysts", "None")).strip() or "None",
                str(data.get("verdict", "Neutral")).strip() or "Neutral"
            )
    except Exception:
        pass

    # Try Python literal dict (as may be saved from dict string in CSV)
    try:
        data = ast.literal_eval(text)
        if isinstance(data, dict):
            return (
                str(data.get("red_flags", "None")).strip() or "None",
                str(data.get("catalysts", "None")).strip() or "None",
                str(data.get("verdict", "Neutral")).strip() or "Neutral"
            )
    except Exception:
        pass

    # Fallback parse manually from simple text
    red_flags = "None"
    catalysts = "None"
    verdict   = "Neutral"

    for line in text.split("\n"):
        line = line.strip()
        if line.upper().startswith("RED FLAGS:") or line.upper().startswith("RED_FLAGS:"):
            acl = line.split(":", 1)
            red_flags = acl[1].strip() if len(acl) > 1 else "None"
        elif line.upper().startswith("CATALYSTS:"):
            acl = line.split(":", 1)
            catalysts = acl[1].strip() if len(acl) > 1 else "None"
        elif line.upper().startswith("VERDICT:"):
            acl = line.split(":", 1)
            verdict = acl[1].strip() if len(acl) > 1 else "Neutral"

    return red_flags or "None", catalysts or "None", verdict or "Neutral"


def build_screen_label(row):
    priority = safe_str(row.get("priority", "—"))
    scanners = safe_str(row.get("scanners", ""))
    if scanners:
        return f"{priority} | {scanners}"
    return priority


def get_ipo_label(row):
    ipo_path = "output/ipo_lookup.csv"
    try:
        if not os.path.exists(ipo_path):
            return "—"

        ipo_df     = pd.read_csv(ipo_path)
        stock_name = safe_str(row.get("stock_name", ""))
        if not stock_name:
            return "—"

        # stop words — too generic to match on
        stop = {"ltd", "limited", "india", "indian", "and", "of",
                "the", "co", "corp", "corporation"}

        def sig_words(name):
            words = str(name).lower().replace(".", "").replace("-", " ").split()
            return [w for w in words if w not in stop and len(w) >= 3][:2]

        stock_sig = sig_words(stock_name)
        if not stock_sig:
            return "—"

        for _, ipo_row in ipo_df.iterrows():
            ipo_sig = sig_words(ipo_row["company_name"])
            if not ipo_sig:
                continue
            # first significant word must match exactly
            if stock_sig[0] != ipo_sig[0]:
                continue
            # if both have 2 words, second must also match
            if len(stock_sig) >= 2 and len(ipo_sig) >= 2:
                if stock_sig[1] != ipo_sig[1]:
                    continue
            qib = ipo_row["qib_subscription"]
            if qib > 25:
                return f"High QIB ({round(float(qib), 1)}x)"

    except Exception:
        pass
    return "—"


def build_fundamentals(row):
    roe      = safe_float(row.get("ROE%"))
    roce     = safe_float(row.get("ROCE%"))
    de       = safe_float(row.get("Debt / Eq"))
    promoter = safe_float(row.get("Prom. Hold.%"))
    pe       = safe_float(row.get("P/E"))
    ind_pe   = safe_float(row.get("Ind PE"))
    sales_g  = safe_float(row.get("Sales growth %"))
    profit_g = safe_float(row.get("Profit growth %"))
    int_cov  = safe_float(row.get("Int Coverage"))
    cfo      = safe_float(row.get("CF Operations Rs.Cr."))
    mar_cap  = safe_float(row.get("Mar Cap Rs.Cr."))

    q1 = safe_float(row.get("Sales Qtr Rs.Cr."))
    q2 = safe_float(row.get("Sales 2Qtr Bk Rs.Cr."))
    q3 = safe_float(row.get("Sales 3Qtr Bk Rs.Cr."))

    if q1 and q2 and q3:
        if q1 > q2 > q3:
            trend = "↑↑ Consistently growing"
        elif q1 > q2:
            trend = "↑ Growing (last 2 qtrs)"
        elif q1 < q2 < q3:
            trend = "↓↓ Consistently declining"
        elif q1 < q2:
            trend = "↓ Declining (last qtr)"
        else:
            trend = "→ Flat"
    else:
        trend = "— Data unavailable"

    lines = [
        "=== PROFITABILITY ===",
        f"ROE          : {roe}%",
        f"ROCE         : {roce}%",
        "",
        "=== VALUATION ===",
        f"P/E          : {pe}x  (Industry: {ind_pe}x)",
        f"P/E vs Ind   : {'Below' if pe and ind_pe and pe < ind_pe * 2 else 'Above'} 2x industry",
        "",
        "=== SAFETY ===",
        f"Debt/Equity  : {de}",
        f"Int Coverage : {int_cov}x",
        f"Promoter     : {promoter}%",
        "",
        "=== GROWTH ===",
        f"Sales growth : {sales_g}% (3yr)",
        f"Profit growth: {profit_g}% (3yr)",
        "",
        "=== QUARTERLY REVENUE TREND ===",
        f"Latest Qtr   : Rs.{q1} Cr",
        f"2 Qtrs back  : Rs.{q2} Cr",
        f"3 Qtrs back  : Rs.{q3} Cr",
        f"Trend        : {trend}",
        "",
        "=== OTHER ===",
        f"Market Cap   : Rs.{mar_cap} Cr",
        f"OCF          : Rs.{cfo} Cr",
    ]
    return "\n".join(str(l) for l in lines)


def is_ai_analysis_available(analysis_dict):
    """Check if AI analysis contains real data vs error messages"""
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
    
    red_flags = str(analysis_dict.get("red_flags", "")).lower().strip()
    catalysts = str(analysis_dict.get("catalysts", "")).lower().strip()
    
    # Check if any field indicates an error
    if any(indicator.lower() in red_flags for indicator in bad_indicators) or \
       any(indicator.lower() in catalysts for indicator in bad_indicators):
        return False
        
    # Check if we have at least some meaningful content
    return not (red_flags in ["none", ""] and 
                catalysts in ["none", ""] and 
                str(analysis_dict.get("verdict", "")).lower().strip() == "neutral")


def push_company(row, scan_date):
    name     = safe_str(row.get("stock_name", "Unknown"))
    symbol   = safe_str(row.get("symbol", ""))
    score    = safe_float(row.get("final_score", 0), 4) or 0
    market   = safe_str(row.get("market_condition", "UNKNOWN"))
    title    = f"{symbol} — {name}"

    screen_label = build_screen_label(row)[:100]
    ipo_label    = get_ipo_label(row)

    red_flags, catalysts, verdict = parse_ai_narrative(
        row.get("ai_narrative", {})
    )

    # Check if AI analysis is actually available
    ai_data = row.get("ai_narrative", {})
    if isinstance(ai_data, str):
        try:
            import json
            ai_data = json.loads(ai_data)
        except:
            ai_data = {}
    
    ai_available = is_ai_analysis_available(ai_data)
    
    # If AI analysis is not available, use placeholder values
    if not ai_available:
        red_flags = "—"
        catalysts = "—"
        verdict = "Neutral"
        print(f"  [AI] {symbol} — using placeholders (no valid analysis)")

    verdict_clean = "Neutral"
    if "bullish" in verdict.lower():
        verdict_clean = "Bullish"
    elif "bearish" in verdict.lower():
        verdict_clean = "Bearish"

    fundamentals = build_fundamentals(row)

    existing = notion.databases.query(
        database_id=NOTION_DATABASE_ID,
        filter={
            "property": "Company",
            "title": {"equals": title}
        }
    )

    properties = {
        "Company": {
            "title": [{"text": {"content": title}}]
        },
        "Screen": {
            "select": {"name": screen_label}
        },
        "Score": {
            "number": round(score * 100, 1)
        },
        "Sector": {
            "select": {"name": "—"}
        },
        "Sector Strength": {
            "select": {"name": "—"}
        },
        "Market Condition": {
            "select": {"name": market}
        },
        "Fundamentals": {
            "rich_text": [{"text": {"content": fundamentals[:2000]}}]
        },
        "Red Flags": {
            "rich_text": [{"text": {"content": red_flags[:2000]}}]
        },
        "Catalysts": {
            "rich_text": [{"text": {"content": catalysts[:2000]}}]
        },
        "Verdict": {
            "select": {"name": verdict_clean}
        },
        "IPO": {
            "select": {"name": ipo_label[:100]}
        },
        "Status": {
            "select": {"name": "To Review"}
        },
        "Scan Date": {
            "date": {"start": scan_date}
        },
        "Notes": {
            "rich_text": [{"text": {"content": ""}}]
        },
    }
    if existing["results"]:
        page_id = existing["results"][0]["id"]
        notion.pages.update(page_id=page_id, properties=properties)
        return "updated"
    else:
        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=properties
        )
        return "created"


def push_to_notion(market_condition="UNKNOWN", ranked_df=None):
    print("=== Notion Push ===\n")
    scan_date = datetime.today().strftime("%Y-%m-%d")

    if ranked_df is None:
        path = "output/final_ranked.csv"
        if not os.path.exists(path):
            print(f"✗ {path} not found — run main.py first")
            return
        ranked_df = pd.read_csv(path)

    total_created = 0
    total_updated = 0
    total_failed  = 0

    for _, row in ranked_df.iterrows():
        symbol = safe_str(row.get("symbol", "Unknown"))
        try:
            result = push_company(row, scan_date)
            icon   = "✓" if result == "created" else "↺"
            if result == "created":
                total_created += 1
            else:
                total_updated += 1
            score = safe_float(row.get("final_score", 0), 4) or 0
            print(f"  {icon} {symbol:12} score: {round(score*100,1)}%")
        except Exception as e:
            print(f"  ✗ {symbol} — {e}")
            total_failed += 1

    print()
    print("─" * 40)
    print(f"  Created : {total_created}")
    print(f"  Updated : {total_updated}")
    print(f"  Failed  : {total_failed}")
    print("─" * 40)
    print(f"\n✓ Open Notion → Trading Candidates to review")


if __name__ == "__main__":
    market_condition = "UNKNOWN"
    try:
        with open("output/market_condition.txt", "r") as f:
            market_condition = f.read().strip()
    except:
        pass
    push_to_notion(market_condition=market_condition)