import os
import json
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

from fetcher import get_session, fetch_screen
from cleaner import clean_dataframe
from market_breadth import check_market_breadth
from chartink_reader import read_chartink_exports, print_summary
from notion_push import push_to_notion
from config import GATE_SCREEN, WEIGHT_TECHNICAL, WEIGHT_FUNDAMENTAL
from sector_strength import enrich_with_sector_strength
from reliability_utils import cache_read_enveloped, cache_write_enveloped, write_json

load_dotenv()

EMAIL = os.getenv("SCREENER_EMAIL")
PASSWORD = os.getenv("SCREENER_PASSWORD")

os.makedirs("output", exist_ok=True)
TRACKED_SYMBOLS_PATH = "output/tracked_symbols.csv"

STOP_WORDS = {
    "limited", "ltd", "ltd.", "and", "of", "the", "for",
    "corporation", "corp", "co",
}

ETF_KEYWORDS = [
    "ETF", "BEES", "LIQUID", "SDL", "CPSE", "GOLDBEES",
    "NIFTYBEES", "JUNIORBEES", "BANKBEES"
]


def load_gate_screen_cached(session):
    cache_path = "output/gate_screen_cache.csv"
    meta_path = "output/gate_screen_cache.meta.json"
    today = datetime.now().strftime("%Y-%m-%d")

    if os.path.exists(cache_path) and os.path.exists(meta_path):
        meta, malformed = cache_read_enveloped(
            meta_path,
            expected_source="main_gate_screen_meta",
            default_data={}
        )
        if malformed:
            meta = {}
        cached_date = meta.get("date")
        if cached_date == today:
            df = pd.read_csv(cache_path)
            print(f"✓ Gate screen loaded from cache — {len(df)} companies")
            return df

    print("  Cache miss — fetching gate screen from Screener...")
    raw_df = fetch_screen(session, GATE_SCREEN["id"], GATE_SCREEN["slug"])
    if raw_df is None:
        return None

    gate_df = clean_dataframe(raw_df)
    gate_df.to_csv(cache_path, index=False)
    cache_write_enveloped(
        meta_path,
        "main_gate_screen_meta",
        {"date": today, "rows": len(gate_df)}
    )
    print(f"✓ Gate screen fetched and cached — {len(gate_df)} companies")
    return gate_df


def load_nse_reference():
    path = "nse_symbols.csv"
    if not os.path.exists(path):
        print("  ⚠ nse_symbols.csv not found")
        return None

    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip().str.upper()
    df["NAME OF COMPANY"] = df["NAME OF COMPANY"].astype(str).str.strip().str.upper()
    print(f"  NSE reference loaded — {len(df)} symbols")
    return df


def get_search_key(symbol, nse_df):
    if nse_df is None:
        return None

    match = nse_df[nse_df["SYMBOL"] == symbol]
    if match.empty:
        return None

    name = match.iloc[0]["NAME OF COMPANY"]
    words = name.split()
    sig = []

    for w in words:
        clean = w.lower().strip(".,&()")
        if clean not in STOP_WORDS and len(clean) >= 2:
            sig.append(clean)
        if len(sig) == 2:
            break

    return " ".join(sig) if sig else None


def technical_conviction_score(row):
    count = int(row.get("scanner_count", 1))
    priority = str(row.get("priority", "")).lower()

    if count == 3:
        return 1.0
    if "stage2" in priority and "institutions" in priority:
        return 0.9
    if "stage2" in priority and "base" in priority:
        return 0.8
    if "institutions" in priority and "base" in priority:
        return 0.75
    return 0.6


def _num(value, default=0.0):
    val = pd.to_numeric(value, errors="coerce")
    if pd.isna(val):
        return default
    return float(val)


def score_fundamentals(row):
    score = 0.0
    max_score = 0.0

    def add(condition, points):
        nonlocal score, max_score
        max_score += points
        if condition:
            score += points

    roe = _num(row.get("ROE%", 0), 0)
    roce = _num(row.get("ROCE%", 0), 0)
    de = _num(row.get("Debt / Eq", 999), 999)
    promoter = _num(row.get("Prom. Hold.%", 0), 0)
    sales_g = _num(row.get("Sales growth %", 0), 0)
    profit_g = _num(row.get("Profit growth %", 0), 0)
    pe = _num(row.get("P/E", 999), 999)
    ind_pe = _num(row.get("Ind PE", 1), 1)

    add(roe > 25, 2)
    add(roe > 15, 1)
    add(roe > 10, 0.5)

    add(roce > 25, 2)
    add(roce > 15, 1)
    add(roce > 10, 0.5)

    add(de < 0.3, 2)
    add(de < 0.7, 1)
    add(de < 1.5, 0.5)

    add(promoter > 60, 2)
    add(promoter > 50, 1)
    add(promoter > 40, 0.5)

    add(sales_g > 20, 2)
    add(sales_g > 10, 1)
    add(sales_g > 0, 0.5)

    add(profit_g > 20, 2)
    add(profit_g > 10, 1)
    add(profit_g > 0, 0.5)

    add(pe < ind_pe * 2, 1)
    add(pe < ind_pe * 1.5, 1)

    q_curr = _num(row.get("Sales Qtr Rs.Cr.", 0), 0)
    q_prev = _num(row.get("Sales 2Qtr Bk Rs.Cr.", 0), 0)
    q_prev2 = _num(row.get("Sales 3Qtr Bk Rs.Cr.", 0), 0)

    if q_curr > 0 and q_prev > 0 and q_prev2 > 0:
        add(q_curr > q_prev > q_prev2, 3)
        add(q_curr > q_prev, 1)

    if max_score == 0:
        return 0.0

    return round(score / max_score, 4)


def fuzzy_match_stock(c_symbol, c_stock_name, gate_df, nse_df):
    search_key = get_search_key(c_symbol, nse_df)

    if not search_key:
        words = c_stock_name.upper().split()
        sig = []
        for w in words:
            clean = w.lower().strip(".,&()")
            if clean not in STOP_WORDS and len(clean) >= 3:
                sig.append(clean)
            if len(sig) == 2:
                break
        search_key = " ".join(sig) if sig else None

    if not search_key:
        return None

    match = gate_df.copy()
    for word in search_key.split():
        match = match[match["Name_upper"].str.contains(word.upper(), na=False)]

    if len(match) == 1:
        return match.iloc[0].to_dict(), "fuzzy_unique", 0.70

    if len(match) > 1:
        best = match.iloc[
            (match["Name_upper"].str.len() - len(c_stock_name.upper())).abs().argsort()[:1]
        ].iloc[0]
        return best.to_dict(), "fuzzy_best_of_many", 0.55

    return None


def run_pipeline():
    print("=== Trading Agent — Full Pipeline ===\n")
    run_report = {
        "started_at": datetime.now().isoformat(),
        "status": "running",
        "steps": {},
    }

    try:
        gate_passes, market_condition, breadth_results = check_market_breadth()
        with open("output/market_condition.txt", "w") as f:
            f.write(market_condition)
        run_report["steps"]["market_breadth"] = {
            "status": "ok",
            "market_condition": market_condition,
        }
    except Exception as e:
        gate_passes, market_condition, breadth_results = False, "UNKNOWN", {}
        run_report["steps"]["market_breadth"] = {
            "status": "error",
            "error": str(e),
        }
    print()

    print("=== Chartink Exports ===\n")
    try:
        chartink_df = read_chartink_exports()
    except Exception as e:
        chartink_df = None
        run_report["steps"]["chartink_exports"] = {"status": "error", "error": str(e)}

    if chartink_df is None:
        run_report["status"] = "failed"
        run_report["steps"]["chartink_exports"] = run_report["steps"].get(
            "chartink_exports", {"status": "error", "error": "No Chartink data"}
        )
        run_report["finished_at"] = datetime.now().isoformat()
        write_json("output/run_report.json", run_report)
        print("✗ No Chartink data — put CSVs in chartink_exports/ and retry")
        return run_report

    before = len(chartink_df)
    chartink_df = chartink_df[
        ~chartink_df["symbol"].str.upper().str.contains("|".join(ETF_KEYWORDS), na=False)
    ].copy()
    removed = before - len(chartink_df)
    if removed > 0:
        print(f"  Filtered out {removed} ETFs/index funds")

    print_summary(chartink_df)
    run_report["steps"]["chartink_exports"] = {
        "status": "ok",
        "rows_after_filter": int(len(chartink_df))
    }
    chartink_df.to_csv("output/chartink_combined.csv", index=False)
    print(f"Total stocks after ETF filter: {len(chartink_df)}\n")

    print("=== Fundamental Gate Screen ===\n")
    session = get_session(EMAIL, PASSWORD)
    if not session:
        run_report["status"] = "failed"
        run_report["steps"]["fundamental_gate"] = {"status": "error", "error": "Screener login failed"}
        run_report["finished_at"] = datetime.now().isoformat()
        write_json("output/run_report.json", run_report)
        print("✗ Could not log into Screener")
        return run_report

    gate_df = load_gate_screen_cached(session)
    if gate_df is None:
        run_report["status"] = "failed"
        run_report["steps"]["fundamental_gate"] = {"status": "error", "error": "Could not fetch gate screen"}
        run_report["finished_at"] = datetime.now().isoformat()
        write_json("output/run_report.json", run_report)
        print("✗ Could not fetch gate screen")
        return run_report

    run_report["steps"]["fundamental_gate"] = {"status": "ok", "rows": int(len(gate_df))}
    gate_df.to_csv("output/gate_screen_clean.csv", index=False)
    gate_df["Name_upper"] = gate_df["Name"].astype(str).str.strip().str.upper()
    print()

    print("=== Intersection — Chartink ∩ Screener ===\n")
    nse_df = load_nse_reference()

    matched_rows = []

    for _, c_row in chartink_df.iterrows():
        c_symbol = str(c_row["symbol"]).strip().upper()
        c_stock_name = str(c_row["stock_name"]).strip()

        resolved = fuzzy_match_stock(c_symbol, c_stock_name, gate_df, nse_df)
        if resolved is not None:
            gate_match, match_method, match_confidence = resolved
            combined = {**c_row.to_dict(), **gate_match}
            combined["match_method"] = match_method
            combined["match_confidence"] = match_confidence
            matched_rows.append(combined)
            print(f"  ✓ {c_symbol:12} → {gate_match['Name']} [{match_method}]")
        else:
            print(f"  ✗ {c_symbol:12} → not in Screener gate")

    print(f"\n  {len(matched_rows)} stocks matched\n")

    if not matched_rows:
        run_report["status"] = "failed"
        run_report["steps"]["intersection"] = {"status": "error", "error": "No matches found"}
        run_report["finished_at"] = datetime.now().isoformat()
        write_json("output/run_report.json", run_report)
        print("✗ No matches found")
        return run_report

    run_report["steps"]["intersection"] = {"status": "ok", "matched_rows": int(len(matched_rows))}
    matched_df = pd.DataFrame(matched_rows)

    print("=== Scoring ===\n")
    matched_df["tech_score"] = matched_df.apply(technical_conviction_score, axis=1)
    matched_df["funda_score"] = matched_df.apply(score_fundamentals, axis=1)

    matched_df["final_score"] = (
        WEIGHT_TECHNICAL * matched_df["tech_score"] +
        WEIGHT_FUNDAMENTAL * matched_df["funda_score"]
    ).round(4)
    matched_df["market_condition"] = market_condition

    matched_df = matched_df.sort_values(
        ["final_score", "scanner_count"],
        ascending=[False, False]
    )

    tracked_cols = ["symbol", "stock_name", "scanner_count", "scanners", "priority"]
    tracked_cols = [c for c in tracked_cols if c in matched_df.columns]
    matched_df[tracked_cols].drop_duplicates(subset=["symbol"]).to_csv(
        TRACKED_SYMBOLS_PATH,
        index=False
    )

    display_cols = [
        "symbol", "stock_name", "final_score", "tech_score", "funda_score",
        "scanner_count", "priority"
    ]
    display_cols = [c for c in display_cols if c in matched_df.columns]
    print("Top 15:")
    print(matched_df[display_cols].head(15).to_string(index=False))
    run_report["steps"]["scoring"] = {"status": "ok", "ranked_rows": int(len(matched_df))}

    print("\n=== Sector Mapping & Candidate Sector Snapshot ===\n")
    try:
        matched_df = enrich_with_sector_strength(matched_df)
        preview_cols = [
            "symbol", "sector", "stock_perf_1m",
            "candidate_sector_avg_1m",
            "candidate_sector_vs_nifty500",
            "candidate_sector_strength_label"
        ]
        preview_cols = [c for c in preview_cols if c in matched_df.columns]
        if preview_cols:
            print(matched_df[preview_cols].head(15).to_string(index=False))
        run_report["steps"]["sector_strength"] = {"status": "ok"}
    except Exception as e:
        run_report["steps"]["sector_strength"] = {"status": "error", "error": str(e)}

    print()
    try:
        from news_analyzer import run_news_analysis
        symbols_to_analyse = matched_df["symbol"].tolist()
        ai_results = run_news_analysis(symbols_to_analyse)

        def get_ai_data(symbol):
            symbol_str = str(symbol).strip().upper()
            if symbol_str in ai_results:
                data = ai_results[symbol_str]
            else:
                data = None
                for key in ai_results.keys():
                    if str(key).strip().upper() == symbol_str:
                        data = ai_results[key]
                        break
                if data is None:
                    data = {
                        "red_flags": "No data available",
                        "catalysts": "No data available",
                        "verdict": "Neutral"
                    }
            return json.dumps(data)

        matched_df["ai_narrative"] = matched_df["symbol"].apply(get_ai_data)
    except Exception as e:
        print(f"  ⚠ AI analysis failed: {e}")
        print("  Continuing with placeholder AI data...")
        placeholder = json.dumps({
            "red_flags": "Analysis failed",
            "catalysts": "Analysis failed",
            "verdict": "Neutral"
        })
        matched_df["ai_narrative"] = placeholder
        run_report["steps"]["news_analysis"] = {"status": "error", "error": str(e)}
    else:
        run_report["steps"]["news_analysis"] = {"status": "ok"}

    final_column_order = [
        "symbol",
        "stock_name",
        "final_score",
        "tech_score",
        "funda_score",
        "scanner_count",
        "scanners",
        "priority",
        "market_condition",
        "sector",
        "stock_perf_1m",
        "candidate_sector_avg_1m",
        "nifty500_perf_1m",
        "candidate_sector_vs_nifty500",
        "candidate_sector_strength_label",
        "ROE%",
        "ROCE%",
        "Debt / Eq",
        "Prom. Hold.%",
        "P/E",
        "Ind PE",
        "Sales growth %",
        "Profit growth %",
        "Int Coverage",
        "CF Operations Rs.Cr.",
        "Mar Cap Rs.Cr.",
        "Sales Qtr Rs.Cr.",
        "Sales 2Qtr Bk Rs.Cr.",
        "Sales 3Qtr Bk Rs.Cr.",
        "ai_narrative",
    ]

    existing_cols = [c for c in final_column_order if c in matched_df.columns]
    remaining_cols = [c for c in matched_df.columns if c not in existing_cols]
    matched_df = matched_df[existing_cols + remaining_cols]

    matched_df.to_csv("output/final_ranked.csv", index=False)

    try:
        push_stats = push_to_notion(market_condition=market_condition, ranked_df=matched_df)
        run_report["steps"]["notion_push"] = {"status": "ok", "stats": push_stats or {}}
    except Exception as e:
        run_report["steps"]["notion_push"] = {"status": "error", "error": str(e)}

    run_report["status"] = "completed"
    run_report["finished_at"] = datetime.now().isoformat()
    run_report["rows_final"] = int(len(matched_df))
    write_json("output/run_report.json", run_report)

    print(f"\n✓ Pipeline complete — {len(matched_df)} companies")
    print(f"  Market condition: {market_condition}")
    return run_report


if __name__ == "__main__":
    run_pipeline()
