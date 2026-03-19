import os
import time
import pandas as pd
from dotenv import load_dotenv
from fetcher import get_session, fetch_screen
from cleaner import clean_dataframe
from config import SCREEN_1, SCREEN_2
from scoring import score_mature, score_midcap
from market_breadth import check_market_breadth

load_dotenv()

EMAIL    = os.getenv("SCREENER_EMAIL")
PASSWORD = os.getenv("SCREENER_PASSWORD")

os.makedirs("output", exist_ok=True)


def load_ipo_lookup():
    path = "output/ipo_lookup.csv"
    if not os.path.exists(path):
        print("⚠ IPO lookup not found — run ipo_fetcher.py first")
        print("  Scoring will run without IPO bonus\n")
        return None
    df = pd.read_csv(path)
    print(f"✓ IPO lookup loaded — {len(df)} companies")
    qib_high = len(df[df['qib_subscription'] > 50])
    qib_mid  = len(df[(df['qib_subscription'] >= 25) & (df['qib_subscription'] <= 50)])
    print(f"  QIB > 50x: {qib_high} | QIB 25-50x: {qib_mid}\n")
    return df


def run_fetch():
    session1 = get_session(EMAIL, PASSWORD)
    if not session1:
        return

    df1 = fetch_screen(session1, SCREEN_1["id"], SCREEN_1["slug"])
    if df1 is not None:
        df1.to_csv("output/screen1_raw.csv", index=False)
        df1 = clean_dataframe(df1)
        df1.to_csv("output/screen1_clean.csv", index=False)
        print(f"✓ Screen 1 — {len(df1)} companies")

    print("\nStarting fresh session for Screen 2...\n")
    time.sleep(3)

    session2 = get_session(EMAIL, PASSWORD)
    if not session2:
        return

    df2 = fetch_screen(session2, SCREEN_2["id"], SCREEN_2["slug"])
    if df2 is not None:
        df2.to_csv("output/screen2_raw.csv", index=False)
        df2 = clean_dataframe(df2)
        df2.to_csv("output/screen2_clean.csv", index=False)
        print(f"✓ Screen 2 — {len(df2)} companies")


def run_scoring(ipo_lookup):
    df1 = pd.read_csv("output/screen1_clean.csv")
    df1 = score_mature(df1)
    df1.to_csv("output/screen1_ranked.csv", index=False)

    print("\n=== SCREEN 1 — Mature Companies (Top 15) ===")
    cols1 = ["Name", "final_score", "Sales growth %",
             "Profit growth %", "momentum_score", "flag"]
    cols1 = [c for c in cols1 if c in df1.columns]
    print(df1[cols1].head(15).to_string(index=False))

    df2 = pd.read_csv("output/screen2_clean.csv")
    df2 = score_midcap(df2, ipo_lookup=ipo_lookup)
    df2.to_csv("output/screen2_ranked.csv", index=False)

    print("\n=== SCREEN 2 — Midcap Growth Companies (Top 15) ===")
    cols2 = ["Name", "final_score", "Sales growth %",
             "Profit growth %", "momentum_score", "ipo_bonus", "flag"]
    cols2 = [c for c in cols2 if c in df2.columns]
    print(df2[cols2].head(15).to_string(index=False))

    ipo_boosted = df2[df2["ipo_bonus"] > 0]
    if len(ipo_boosted) > 0:
        print(f"\n=== IPO BONUS APPLIED ({len(ipo_boosted)} companies) ===")
        print(ipo_boosted[["Name", "final_score", "ipo_bonus", "flag"]].to_string(index=False))

    return df1, df2


if __name__ == "__main__":
    print("=== Trading Agent — Full Pipeline ===\n")

    # step 1 — market breadth check (advisory only, never halts)
    gate_passes, market_condition, breadth_results = check_market_breadth()

    # step 2 — load IPO lookup
    ipo_lookup = load_ipo_lookup()

    # step 3 — fetch data from Screener
    run_fetch()

    # step 4 — score and rank
    df1, df2 = run_scoring(ipo_lookup)

    print(f"\n✓ Pipeline complete")
    print(f"  Market condition today: {market_condition}")
    if market_condition == "RISKY":
        print("  ⚠ Companies are tagged RISKY in Notion")
        print("  Consider waiting or trading with reduced position size")
    print("  Next step: run notion_push.py to send results to Notion")