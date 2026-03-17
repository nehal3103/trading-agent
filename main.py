import os
import time
from dotenv import load_dotenv
from fetcher import get_session, fetch_screen
from cleaner import clean_dataframe
from config import SCREEN_1, SCREEN_2
from scoring import score_mature, score_young
import pandas as pd


load_dotenv()

EMAIL = os.getenv("SCREENER_EMAIL")
PASSWORD = os.getenv("SCREENER_PASSWORD")


def run():
    # -------- Screen 1 --------
    session1 = get_session(EMAIL, PASSWORD)
    if not session1:
        return

    df1 = fetch_screen(session1, SCREEN_1["id"], SCREEN_1["slug"])

    if df1 is not None:
        df1.to_csv("output/screen1_raw.csv", index=False)

        df1 = clean_dataframe(df1)
        df1.to_csv("output/screen1_clean.csv", index=False)

        print("\nScreen1 null check:")
        print(df1.isnull().sum().sort_values(ascending=False).head(10))

    print("\n🔄 Starting fresh session for screen 2...\n")
    time.sleep(3)

    session2 = get_session(EMAIL, PASSWORD)
    if not session2:
        return

    df2 = fetch_screen(session2, SCREEN_2["id"], SCREEN_2["slug"])

    if df2 is not None:
        df2.to_csv("output/screen2_raw.csv", index=False)

        df2 = clean_dataframe(df2)
        df2.to_csv("output/screen2_clean.csv", index=False)

        print("\nScreen2 null check:")
        print(df2.isnull().sum().sort_values(ascending=False).head(10))

# -------- Scoring --------
def run_scoring():
    # Screen 1: Mature Companies
    df1 = pd.read_csv("output/screen1_clean.csv")
    df1 = score_mature(df1)
    df1 = df1.sort_values("final_score", ascending=False)
    df1.to_csv("output/screen1_ranked.csv", index=False)
    print("✓ Screen 1 (Mature) scoring complete")

    # Screen 2: Young Companies
    df2 = pd.read_csv("output/screen2_clean.csv")
    df2 = score_young(df2)
    df2 = df2.sort_values("final_score", ascending=False)
    df2.to_csv("output/screen2_ranked.csv", index=False)
    print("✓ Screen 2 (Young) scoring complete")



if __name__ == "__main__":
    #run()
    run_scoring()