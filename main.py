import os
import time
import pandas as pd
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

# strict stop words — keep 'india' so 'Engineers India' doesnt match 'Rajoo Engineers'
STOP_WORDS = {
    "limited", "ltd", "ltd.", "and", "of", "the", "for",
    "corporation", "corp", "co",
}

# keywords to filter out ETFs and index funds from Chartink
ETF_KEYWORDS = ["ETF", "BEES", "LIQUID", "SDL", "CPSE", "GOLDBEES",
                "NIFTYBEES", "JUNIORBEES", "BANKBEES"]


# ─────────────────────────────────────────────
# LOAD NSE REFERENCE
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


# ─────────────────────────────────────────────
# GET 2-WORD SEARCH KEY FROM NSE NAME
# ─────────────────────────────────────────────
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

    # filter out ETFs and index funds
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

    # step 3 — fundamental gate screen
    print("=== Fundamental Gate Screen ===\n")
    session = get_session(EMAIL, PASSWORD)
    if not session:
        print("✗ Could not log into Screener")
        exit(0)

    raw_df = fetch_screen(session, GATE_SCREEN["id"], GATE_SCREEN["slug"])
    if raw_df is None:
        print("✗ Could not fetch gate screen")
        exit(0)

    gate_df = clean_dataframe(raw_df)
    gate_df.to_csv("output/gate_screen_clean.csv", index=False)
    print(f"✓ Gate screen — {len(gate_df)} companies\n")
    gate_df["Name_upper"] = gate_df["Name"].str.strip().str.upper()

    # step 4 — intersection
    print("=== Intersection — Chartink ∩ Screener ===\n")
    nse_df = load_nse_reference()

    matched_rows = []

    for _, c_row in chartink_df.iterrows():
        c_symbol     = str(c_row["symbol"]).strip().upper()
        c_stock_name = str(c_row["stock_name"]).strip()

        search_key = get_search_key(c_symbol, nse_df)

        if not search_key:
            # fallback to Chartink name
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
            print(f"  ✗ {c_symbol:12} → no search key")
            continue

        # require ALL words in search key to match
        search_words = search_key.split()
        match = gate_df.copy()
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

    # --- New IPO Tagging Logic ---
    matched_df["ipo_status"] = ""
    ipo_path = "output/ipo_lookup.csv"

    if os.path.exists(ipo_path):
        ipo_df = pd.read_csv(ipo_path)
        print("  Checking for High QIB IPOs...")

        for idx, row in matched_df.iterrows():
            # Get the first word of the stock name (e.g., 'Zomato' from 'Zomato Ltd')
            stock_first_word = str(row['stock_name']).split()[0].upper()
            
            # Look for a match in the IPO table (if company_name contains first word)
            match = ipo_df[ipo_df['company_name'].str.upper().str.contains(stock_first_word, na=False)]
            
            if not match.empty:
                qib_val = match.iloc[0]['qib_subscription']
                if qib_val > 25:
                    matched_df.at[idx, "ipo_status"] = f"🚀 IPO (QIB: {qib_val}x)"
                    print(f"  ✦ {row['symbol']:12} matched recent high-demand IPO!")
    # -----------------------------

    matched_df = matched_df.sort_values("final_score", ascending=False)
    matched_df.to_csv("output/final_ranked.csv", index=False)

    print("\nTop 15:")
    display_cols = ["symbol", "stock_name", "final_score",
                    "tech_score", "funda_score", "priority", "ipo_status"]
    display_cols = [c for c in display_cols if c in matched_df.columns]
    print(matched_df[display_cols].head(15).to_string(index=False))

    # ... inside main.py, after Step 5 (Scoring) ...

    import news_analyzer
    import math

    # ... after your DataFrame 'matched_df' is ready ...

    print(f"=== Starting 2026 AI Analysis for {len(matched_df)} stocks ===")

    # 1. First, fetch all the news (this doesn't hit AI limits)
    all_news_data = []
    for idx, row in matched_df.iterrows():
        news_text = news_analyzer.get_realtime_announcements(row['symbol'])
        all_news_data.append({"symbol": row['symbol'], "news": news_text})

    # 2. Process in batches of 5
    batch_size = 5
    final_results = {}

    for i in range(0, len(all_news_data), batch_size):
        batch = all_news_data[i : i + batch_size]
        print(f"  Processing Batch {math.ceil((i+1)/batch_size)}...")

        # Single AI call for 5 stocks
        batch_output = news_analyzer.analyze_stock_batch(batch)

        # Store results (Basic logic: you can refine the parsing later)
        for item in batch:
            final_results[item['symbol']] = batch_output

    # 3. Save back to DataFrame
    matched_df['ai_analysis'] = matched_df['symbol'].map(final_results)

    # step 6 — notion push
    push_to_notion(market_condition=market_condition, ranked_df=matched_df)

    print(f"\n✓ Pipeline complete — {len(matched_df)} companies")
    print(f"  Market condition: {market_condition}")