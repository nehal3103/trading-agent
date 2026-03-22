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
    if isinstance(narrative, dict):
        return (
            narrative.get("red_flags", "None"),
            narrative.get("catalysts", "None"),
            narrative.get("verdict", "Neutral")
        )
    narrative = str(narrative)
    red_flags = "None"
    catalysts = "None"
    verdict   = "Neutral"
    for line in narrative.split("\n"):
        line = line.strip()
        if line.startswith("RED FLAGS:") or line.startswith("RED_FLAGS:"):
            red_flags = line.split(":", 1)[1].strip()
        elif line.startswith("CATALYSTS:"):
            catalysts = line.split(":", 1)[1].strip()
        elif line.startswith("VERDICT:"):
            verdict = line.split(":", 1)[1].strip()
    return red_flags, catalysts, verdict


def build_screen_label(row):
    """
    Shows both priority label and scanner names.
    e.g. "A — Coal India (stage2 + growthstocks + buying_urgency)"
    """
    priority = safe_str(row.get("priority", "—"))
    scanners = safe_str(row.get("scanners", ""))
    if scanners:
        return f"{priority} | {scanners}"
    return priority


def get_ipo_label(row):
    """Returns IPO label if stock has high QIB, else —"""
    ipo_path = "output/ipo_lookup.csv"
    try:
        if not os.path.exists(ipo_path):
            return "—"
        ipo_df     = pd.read_csv(ipo_path)
        stock_name = safe_str(row.get("stock_name", ""))
        first_word = stock_name.split()[0].upper() if stock_name else ""
        if not first_word:
            return "—"
        match = ipo_df[
            ipo_df["company_name"].str.upper().str.contains(first_word, na=False)
        ]
        if not match.empty and match.iloc[0]["qib_subscription"] > 25:
            qib = match.iloc[0]["qib_subscription"]
            return f"High QIB ({qib}x)"
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

    verdict_clean = "Neutral"
    if "bullish" in verdict.lower():
        verdict_clean = "Bullish"
    elif "bearish" in verdict.lower():
        verdict_clean = "Bearish"

    fundamentals = build_fundamentals(row)

    # only update AI Summary if we have real analysis
    # if quota was exhausted this preserves existing Notion data
    has_real_analysis = (
        red_flags not in ('Analysis unavailable', 'None', '')
    )

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
        "Your TA Notes": {
            "rich_text": [{"text": {"content": ""}}]
        },
    }

    # conditionally add AI Summary — only if real analysis available
    if has_real_analysis:
        properties["AI Summary"] = {
            "rich_text": [{"text": {"content": f"Red Flags: {red_flags}Catalysts: {catalysts}Verdict: {verdict_clean}"[:2000]}}]
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