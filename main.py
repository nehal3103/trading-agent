import os
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

from fetcher import get_session, fetch_screen
from cleaner import clean_dataframe
from market_breadth import check_market_breadth
from chartink_reader import read_chartink_exports, print_summary
from notion_push import push_to_notion
from config import GATE_SCREEN, WEIGHT_TECHNICAL, WEIGHT_FUNDAMENTAL

load_dotenv()

EMAIL    = os.getenv("SCREENER_EMAIL")
PASSWORD = os.getenv("SCREENER_PASSWORD")

os.makedirs("output", exist_ok=True)

STOP_WORDS = {
    "limited", "ltd", "ltd.", "and", "of", "the", "for",
    "corporation", "corp", "co",
}

ETF_KEYWORDS = ["ETF", "BEES", "LIQUID", "SDL", "CPSE", "GOLDBEES",
                "NIFTYBEES", "JUNIORBEES", "BANKBEES"]


# ─────────────────────────────────────────────
# SCREENER GATE CACHE
# ─────────────────────────────────────────────
def load_gate_screen_cached(session):
    cache_path = "output/gate_screen_cache.csv"
    meta_path  = "output/gate_screen_cache_date.txt"
    today      = datetime.now().strftime("%Y-%m-%d")

    if os.path.exists(cache_path) and os.path.exists(meta_path):
        cached_date = open(meta_path).read().strip()
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
    open(meta_path, "w").write(today)
    print(f"✓ Gate screen fetched and cached — {len(gate_df)} companies")
    return gate_df


# ─────────────────────────────────────────────
# NSE REFERENCE
# ─────────────────────────────────────────────
def load_nse_reference():
    path = "nse_symbols.csv"
    if not os.path.exists(path):
        print("  ⚠ nse_symbols.csv not found")
        return None
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    df["SYMBOL"]          = df["SYMBOL"].str.strip().str.upper()
    df["NAME OF COMPANY"] = df["NAME OF COMPANY"].str.strip().str.upper()
    print(f"  NSE reference loaded — {len(df)} symbols")
    return df


def get_search_key(symbol, nse_df):
    if nse_df is None:
        return None
    match = nse_df[nse_df["SYMBOL"] == symbol]
    if match.empty:
        return None
    name  = match.iloc[0]["NAME OF COMPANY"]
    words = name.split()
    sig   = []
    for w in words:
        clean = w.lower().strip(".,&()")
        if clean not in STOP_WORDS and len(clean) >= 2:
            sig.append(clean)
        if len(sig) == 2:
            break
    return " ".join(sig) if sig else None


# ─────────────────────────────────────────────
# TECHNICAL CONVICTION SCORE
# ─────────────────────────────────────────────
def technical_conviction_score(row):
    count    = int(row.get("scanner_count", 1))
    priority = str(row.get("priority", ""))
    if count == 3:
        return 1.0
    elif "stage2" in priority and "institutions" in priority:
        return 0.9
    elif "stage2" in priority and "base" in priority:
        return 0.8
    elif "institutions" in priority and "base" in priority:
        return 0.75
    else:
        return 0.6


# ─────────────────────────────────────────────
# FUNDAMENTAL SCORE
# ─────────────────────────────────────────────
def score_fundamentals(row):
    score     = 0.0
    max_score = 0.0

    def add(condition, points):
        nonlocal score, max_score
        max_score += points
        if condition:
            score += points

    roe      = pd.to_numeric(row.get("ROE%", 0), errors="coerce") or 0
    roce     = pd.to_numeric(row.get("ROCE%", 0), errors="coerce") or 0
    de       = pd.to_numeric(row.get("Debt / Eq", 999), errors="coerce") or 999
    promoter = pd.to_numeric(row.get("Prom. Hold.%", 0), errors="coerce") or 0
    sales_g  = pd.to_numeric(row.get("Sales growth %", 0), errors="coerce") or 0
    profit_g = pd.to_numeric(row.get("Profit growth %", 0), errors="coerce") or 0
    pe       = pd.to_numeric(row.get("P/E", 999), errors="coerce") or 999
    ind_pe   = pd.to_numeric(row.get("Ind PE", 1), errors="coerce") or 1

    add(roe > 25, 2);      add(roe > 15, 1);      add(roe > 10, 0.5)
    add(roce > 25, 2);     add(roce > 15, 1);     add(roce > 10, 0.5)
    add(de < 0.3, 2);      add(de < 0.7, 1);      add(de < 1.5, 0.5)
    add(promoter > 60, 2); add(promoter > 50, 1); add(promoter > 40, 0.5)
    add(sales_g > 20, 2);  add(sales_g > 10, 1);  add(sales_g > 0, 0.5)
    add(profit_g > 20, 2); add(profit_g > 10, 1); add(profit_g > 0, 0.5)
    add(pe < ind_pe * 2, 1); add(pe < ind_pe * 1.5, 1)

    q_curr  = pd.to_numeric(row.get("Sales Qtr Rs.Cr.", 0), errors="coerce") or 0
    q_prev  = pd.to_numeric(row.get("Sales 2Qtr Bk Rs.Cr.", 0), errors="coerce") or 0
    q_prev2 = pd.to_numeric(row.get("Sales 3Qtr Bk Rs.Cr.", 0), errors="coerce") or 0
    if q_curr > 0 and q_prev > 0 and q_prev2 > 0:
        add(q_curr > q_prev > q_prev2, 3)
        add(q_curr > q_prev, 1)

    if max_score == 0:
        return 0.0
    return round(score / max_score, 4)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Trading Agent — Full Pipeline ===\n")

    # step 1 — market breadth
    gate_passes, market_condition, breadth_results = check_market_breadth()
    with open("output/market_condition.txt", "w") as f:
        f.write(market_condition)
    print()

    # step 2 — chartink exports
    print("=== Chartink Exports ===\n")
    chartink_df = read_chartink_exports()
    if chartink_df is None:
        print("✗ No Chartink data — put CSVs in chartink_exports/ and retry")
        exit(0)

    before = len(chartink_df)
    chartink_df = chartink_df[
        ~chartink_df["symbol"].str.upper().str.contains(
            "|".join(ETF_KEYWORDS), na=False
        )
    ].copy()
    removed = before - len(chartink_df)
    if removed > 0:
        print(f"  Filtered out {removed} ETFs/index funds")

    print_summary(chartink_df)
    chartink_df.to_csv("output/chartink_combined.csv", index=False)
    print(f"Total stocks after ETF filter: {len(chartink_df)}\n")

    # step 3 — fundamental gate screen (cached)
    print("=== Fundamental Gate Screen ===\n")
    session = get_session(EMAIL, PASSWORD)
    if not session:
        print("✗ Could not log into Screener")
        exit(0)

    gate_df = load_gate_screen_cached(session)
    if gate_df is None:
        print("✗ Could not fetch gate screen")
        exit(0)

    gate_df.to_csv("output/gate_screen_clean.csv", index=False)
    gate_df["Name_upper"] = gate_df["Name"].str.strip().str.upper()
    print()

    # step 4 — intersection
    print("=== Intersection — Chartink ∩ Screener ===\n")
    nse_df = load_nse_reference()

    matched_rows = []
    for _, c_row in chartink_df.iterrows():
        c_symbol     = str(c_row["symbol"]).strip().upper()
        c_stock_name = str(c_row["stock_name"]).strip()

        search_key = get_search_key(c_symbol, nse_df)

        if not search_key:
            words = c_stock_name.upper().split()
            sig   = []
            for w in words:
                clean = w.lower().strip(".,&()")
                if clean not in STOP_WORDS and len(clean) >= 3:
                    sig.append(clean)
                if len(sig) == 2:
                    break
            search_key = " ".join(sig) if sig else None

        if not search_key:
            print(f"  ✗ {c_symbol:12} → no search key")
            continue

        search_words = search_key.split()
        match        = gate_df.copy()
        for word in search_words:
            match = match[
                match["Name_upper"].str.contains(word.upper(), na=False)
            ]

        if len(match) == 1:
            combined = {**c_row.to_dict(), **match.iloc[0].to_dict()}
            matched_rows.append(combined)
            print(f"  ✓ {c_symbol:12} → {match.iloc[0]['Name']}")
        elif len(match) > 1:
            best = match.iloc[
                (match["Name_upper"].str.len() - len(c_stock_name.upper()))
                .abs().argsort()[:1]
            ].iloc[0]
            combined = {**c_row.to_dict(), **best.to_dict()}
            matched_rows.append(combined)
            print(f"  ✓ {c_symbol:12} → {best['Name']} (best of {len(match)})")
        else:
            print(f"  ✗ {c_symbol:12} → not in Screener gate")

    print(f"\n  {len(matched_rows)} stocks matched\n")
    if not matched_rows:
        print("✗ No matches found")
        exit(0)

    matched_df = pd.DataFrame(matched_rows)

    # step 5 — scoring
    print("=== Scoring ===\n")
    matched_df["tech_score"]       = matched_df.apply(technical_conviction_score, axis=1)
    matched_df["funda_score"]      = matched_df.apply(score_fundamentals, axis=1)
    matched_df["final_score"]      = (
        WEIGHT_TECHNICAL   * matched_df["tech_score"] +
        WEIGHT_FUNDAMENTAL * matched_df["funda_score"]
    ).round(4)
    matched_df["market_condition"] = market_condition
    matched_df = matched_df.sort_values("final_score", ascending=False)

    print("Top 15:")
    display_cols = ["symbol", "stock_name", "final_score",
                    "tech_score", "funda_score", "priority"]
    display_cols = [c for c in display_cols if c in matched_df.columns]
    print(matched_df[display_cols].head(15).to_string(index=False))

    # step 5b — news analysis (web search via Groq compound)
    print()
    try:
        from news_analyzer import run_news_analysis
        import json
        symbols_to_analyse = matched_df["symbol"].tolist()
        ai_results         = run_news_analysis(symbols_to_analyse)

        # Debug: check what we got back
        print("\n  DEBUG — AI Results Summary:")
        for sym, data in list(ai_results.items())[:3]:  # Show first 3
            print(f"    {sym}: red_flags={data.get('red_flags', 'MISSING')[:50]}... catalysts={data.get('catalysts', 'MISSING')[:50]}...")
        print(f"    Total symbols analyzed: {len(ai_results)}")

        # Create ai_narrative with proper defaults and serialize to JSON
        def get_ai_data(symbol):
            symbol_str = str(symbol).strip().upper()
            # Try exact match first, then case-insensitive
            if symbol_str in ai_results:
                data = ai_results[symbol_str]
            else:
                # Fallback search
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
        
        # Debug: verify the data in the DataFrame
        if len(matched_df) > 0:
            first_sym = matched_df.iloc[0]["symbol"]
            first_narrative = matched_df.iloc[0]["ai_narrative"]
            print(f"\n  DEBUG — First row in DataFrame:")
            print(f"    Symbol: {first_sym}")
            print(f"    ai_narrative (raw): {first_narrative[:200]}")
            # Try to parse it back
            try:
                parsed = json.loads(first_narrative)
                print(f"    Parsed red_flags: {parsed.get('red_flags', 'MISSING')}")
                print(f"    Parsed catalysts: {parsed.get('catalysts', 'MISSING')}")
            except Exception as e:
                print(f"    ✗ Failed to parse: {e}")

    except Exception as e:
        print(f"  ⚠ AI analysis failed: {e}")
        print("  Continuing with placeholder AI data...")
        # Create placeholder AI data for all stocks
        placeholder = json.dumps({
            "red_flags": "Analysis failed",
            "catalysts": "Analysis failed", 
            "verdict": "Neutral"
        })
        matched_df["ai_narrative"] = placeholder

    matched_df.to_csv("output/final_ranked.csv", index=False)

    matched_df.to_csv("output/final_ranked.csv", index=False)

    # step 6 — notion push
    push_to_notion(market_condition=market_condition, ranked_df=matched_df)

    print(f"\n✓ Pipeline complete — {len(matched_df)} companies")
    print(f"  Market condition: {market_condition}")