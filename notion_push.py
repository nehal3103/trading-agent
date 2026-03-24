import os
import time
import json
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from notion_client import Client
from reliability_utils import write_json

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

notion = Client(auth=NOTION_TOKEN)
_DB_SCHEMA_CACHE = None
_IPO_LOOKUP_CACHE = None


def safe_float(val, decimals=2):
    try:
        return round(float(val), decimals)
    except Exception:
        return None


def safe_str(val):
    if pd.isna(val):
        return ""
    return str(val).strip()


def parse_ai_narrative(narrative):
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

    red_flags = "None"
    catalysts = "None"
    verdict = "Neutral"

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


def notion_retry(callable_fn, retries=3, base_wait=1.5):
    last_error = None
    for attempt in range(retries):
        try:
            return callable_fn()
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            transient = any(k in msg for k in ["timeout", "rate", "503", "429", "tempor"])
            if not transient or attempt == retries - 1:
                raise
            time.sleep(base_wait * (attempt + 1))
    raise last_error


def get_database_schema():
    global _DB_SCHEMA_CACHE
    if _DB_SCHEMA_CACHE is not None:
        return _DB_SCHEMA_CACHE
    try:
        db = notion_retry(lambda: notion.databases.retrieve(database_id=NOTION_DATABASE_ID))
        _DB_SCHEMA_CACHE = db.get("properties", {})
    except Exception:
        _DB_SCHEMA_CACHE = {}
    return _DB_SCHEMA_CACHE


def safe_select_property(properties, prop_name, requested, fallback="Unknown"):
    schema = get_database_schema()
    prop = schema.get(prop_name, {})
    options = (
        prop.get("select", {}).get("options", [])
        if isinstance(prop, dict)
        else []
    )
    allowed = {o.get("name") for o in options if isinstance(o, dict)}
    value = (requested or fallback or "Unknown").strip()[:100]
    if allowed and value not in allowed:
        if fallback in allowed:
            value = fallback
        elif "—" in allowed:
            value = "—"
        else:
            value = next(iter(allowed))
    properties[prop_name] = {"select": {"name": value}}


def _get_ipo_lookup():
    global _IPO_LOOKUP_CACHE
    if _IPO_LOOKUP_CACHE is not None:
        return _IPO_LOOKUP_CACHE

    ipo_path = "output/ipo_lookup.csv"
    if not os.path.exists(ipo_path):
        _IPO_LOOKUP_CACHE = None
        return None

    try:
        _IPO_LOOKUP_CACHE = pd.read_csv(ipo_path)
    except Exception:
        _IPO_LOOKUP_CACHE = None
    return _IPO_LOOKUP_CACHE


def get_ipo_label(row):
    ipo_df = _get_ipo_lookup()
    try:
        if ipo_df is None or ipo_df.empty:
            return "—"

        stock_name = safe_str(row.get("stock_name", ""))
        if not stock_name:
            return "—"

        stop = {"ltd", "limited", "india", "indian", "and", "of", "the", "co", "corp", "corporation"}

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
            if stock_sig[0] != ipo_sig[0]:
                continue
            if len(stock_sig) >= 2 and len(ipo_sig) >= 2 and stock_sig[1] != ipo_sig[1]:
                continue

            qib = ipo_row["qib_subscription"]
            if qib > 25:
                return f"High QIB ({round(float(qib), 1)}x)"
    except Exception:
        pass
    return "—"


def build_fundamentals(row):
    roe = safe_float(row.get("ROE%"))
    roce = safe_float(row.get("ROCE%"))
    de = safe_float(row.get("Debt / Eq"))
    promoter = safe_float(row.get("Prom. Hold.%"))
    pe = safe_float(row.get("P/E"))
    ind_pe = safe_float(row.get("Ind PE"))
    sales_g = safe_float(row.get("Sales growth %"))
    profit_g = safe_float(row.get("Profit growth %"))
    int_cov = safe_float(row.get("Int Coverage"))
    cfo = safe_float(row.get("CF Operations Rs.Cr."))
    mar_cap = safe_float(row.get("Mar Cap Rs.Cr."))

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

    explain_lines = [
        "=== SCORE BREAKDOWN ===",
        f"Tech score              : {safe_float(row.get('tech_score'), 4)}",
        f"Funda score             : {safe_float(row.get('funda_score'), 4)}",
        f"ROE component           : {safe_float(row.get('roe_component'), 4)}",
        f"ROCE component          : {safe_float(row.get('roce_component'), 4)}",
        f"Debt component          : {safe_float(row.get('debt_component'), 4)}",
        f"Promoter component      : {safe_float(row.get('promoter_component'), 4)}",
        f"Growth component        : {safe_float(row.get('growth_component'), 4)}",
        f"Valuation component     : {safe_float(row.get('valuation_component'), 4)}",
        f"Quarterly trend comp.   : {safe_float(row.get('quarterly_trend_component'), 4)}",
        f"Match method            : {safe_str(row.get('match_method', ''))}",
        f"Match confidence        : {safe_float(row.get('match_confidence'), 2)}",
        "",
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
    return "\n".join(str(l) for l in explain_lines)


def is_ai_analysis_available(analysis_dict):
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

    if any(indicator.lower() in red_flags for indicator in bad_indicators) or \
       any(indicator.lower() in catalysts for indicator in bad_indicators):
        return False

    return not (
        red_flags in ["none", ""] and
        catalysts in ["none", ""] and
        str(analysis_dict.get("verdict", "")).lower().strip() == "neutral"
    )


def push_company(row, scan_date):
    name = safe_str(row.get("stock_name", "Unknown"))
    symbol = safe_str(row.get("symbol", ""))
    score = safe_float(row.get("final_score", 0), 4) or 0
    market = safe_str(row.get("market_condition", "UNKNOWN"))
    title = f"{symbol} — {name}"

    screen_label = build_screen_label(row)[:100]
    ipo_label = get_ipo_label(row)

    red_flags, catalysts, verdict = parse_ai_narrative(row.get("ai_narrative", {}))

    ai_data = row.get("ai_narrative", {})
    if isinstance(ai_data, str):
        try:
            ai_data = json.loads(ai_data)
        except Exception:
            ai_data = {}

    ai_available = is_ai_analysis_available(ai_data)
    if not ai_available:
        print(f"  [AI] {symbol} — analysis unavailable; not updating Red Flags/Catalysts/Verdict")

    verdict_clean = "Neutral"
    if "bullish" in verdict.lower():
        verdict_clean = "Bullish"
    elif "bearish" in verdict.lower():
        verdict_clean = "Bearish"

    fundamentals = build_fundamentals(row)
    sector = safe_str(row.get("sector", "Unknown")) or "Unknown"
    sector_strength_label = safe_str(row.get("candidate_sector_strength_label", "Flat")) or "Flat"
    candidate_sector_avg_1m = safe_float(row.get("candidate_sector_avg_1m", 0), 2)
    nifty500_perf_1m = safe_float(row.get("nifty500_perf_1m", 0), 2)
    candidate_sector_vs_nifty500 = safe_float(row.get("candidate_sector_vs_nifty500", 0), 2)

    existing = notion_retry(lambda: notion.databases.query(
        database_id=NOTION_DATABASE_ID,
        filter={
            "property": "Company",
            "title": {"equals": title}
        }
    ))

    properties = {
        "Company": {
            "title": [{"text": {"content": title}}]
        },
        "Screen": {},
        "Score": {
            "number": round(score * 100, 1)
        },
        "Sector": {},
        "Sector Strength": {},
        "Market Condition": {},
        "Fundamentals": {
            "rich_text": [{"text": {"content": fundamentals[:2000]}}]
        },
        "IPO": {},
        "Status": {},
        "Scan Date": {
            "date": {"start": scan_date}
        },
        "Notes": {
            "rich_text": [{
                "text": {
                    "content": (
                        f"Candidate sector avg 1M: {candidate_sector_avg_1m}% | "
                        f"Nifty500 1M: {nifty500_perf_1m}% | "
                        f"Candidate sector alpha vs Nifty500: {candidate_sector_vs_nifty500}%"
                    )
                }
            }]
        },
    }

    safe_select_property(properties, "Screen", screen_label, fallback="—")
    safe_select_property(properties, "Sector", sector[:100], fallback="Unknown")
    safe_select_property(properties, "Sector Strength", sector_strength_label[:100], fallback="Slightly Weak vs Nifty500")
    safe_select_property(properties, "Market Condition", market, fallback="UNKNOWN")
    safe_select_property(properties, "IPO", ipo_label[:100], fallback="—")
    safe_select_property(properties, "Status", "To Review", fallback="To Review")

    if ai_available:
        properties["Red Flags"] = {
            "rich_text": [{"text": {"content": red_flags[:2000]}}]
        }
        properties["Catalysts"] = {
            "rich_text": [{"text": {"content": catalysts[:2000]}}]
        }
        safe_select_property(properties, "Verdict", verdict_clean, fallback="Neutral")

    if existing["results"]:
        page_id = existing["results"][0]["id"]
        notion_retry(lambda: notion.pages.update(page_id=page_id, properties=properties))
        return "updated"
    else:
        notion_retry(lambda: notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=properties
        ))
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
    total_failed = 0
    failures = []

    for _, row in ranked_df.iterrows():
        symbol = safe_str(row.get("symbol", "Unknown"))
        try:
            result = push_company(row, scan_date)
            icon = "✓" if result == "created" else "↺"
            if result == "created":
                total_created += 1
            else:
                total_updated += 1
            score = safe_float(row.get("final_score", 0), 4) or 0
            print(f"  {icon} {symbol:12} score: {round(score * 100, 1)}%")
        except Exception as e:
            print(f"  ✗ {symbol} — {e}")
            total_failed += 1
            failures.append({"symbol": symbol, "error": str(e)})

    print()
    print("─" * 40)
    print(f"  Created : {total_created}")
    print(f"  Updated : {total_updated}")
    print(f"  Failed  : {total_failed}")
    print("─" * 40)
    print("\n✓ Open Notion → Trading Candidates to review")

    stats = {
        "created": total_created,
        "updated": total_updated,
        "failed": total_failed,
        "failed_rows": failures,
        "scan_date": scan_date,
    }
    write_json("output/notion_last_run.json", stats)
    return stats


def retry_failed_from_last_run(market_condition="UNKNOWN", source_csv="output/final_ranked.csv"):
    path = "output/notion_last_run.json"
    if not os.path.exists(path):
        print("✗ No previous Notion run report found.")
        return None

    with open(path, "r") as f:
        data = json.load(f)

    failed_rows = data.get("failed_rows", [])
    if not failed_rows:
        print("✓ No failed Notion rows to retry.")
        return {"retried": 0, "failed": 0}

    failed_symbols = {str(x.get("symbol", "")).strip().upper() for x in failed_rows}
    if not os.path.exists(source_csv):
        print(f"✗ Source CSV missing: {source_csv}")
        return None

    ranked_df = pd.read_csv(source_csv)
    retry_df = ranked_df[ranked_df["symbol"].astype(str).str.upper().isin(failed_symbols)].copy()
    if retry_df.empty:
        print("✗ No matching symbols found for retry.")
        return None

    print(f"Retrying {len(retry_df)} failed symbols...")
    return push_to_notion(market_condition=market_condition, ranked_df=retry_df)


if __name__ == "__main__":
    market_condition = "UNKNOWN"
    try:
        with open("output/market_condition.txt", "r") as f:
            market_condition = f.read().strip()
    except Exception:
        pass
    push_to_notion(market_condition=market_condition)
