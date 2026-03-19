import pandas as pd
import os

# ─────────────────────────────────────────────
# IPO Fetcher — reads manually exported CSVs
# from Chittorgarh for 2024, 2025, 2026
#
# To update: export new year CSV from Chittorgarh
# and add it to the ipo_data/ folder
# ─────────────────────────────────────────────

IPO_DATA_DIR = "ipo_data"
OUTPUT_PATH  = "output/ipo_lookup.csv"


def load_ipo_csvs():
    """
    Reads all CSV files from the ipo_data/ folder.
    Each file should have two columns: Company, QIB (x)
    Filename should include the year e.g. ipo-2024.csv
    """
    if not os.path.exists(IPO_DATA_DIR):
        print(f"✗ Folder '{IPO_DATA_DIR}' not found")
        print(f"  Create it and put your ipo-2024.csv, ipo-2025.csv, ipo-2026.csv inside")
        return None

    files = [f for f in os.listdir(IPO_DATA_DIR) if f.endswith(".csv")]

    if not files:
        print(f"✗ No CSV files found in '{IPO_DATA_DIR}/'")
        return None

    all_dfs = []

    for filename in sorted(files):
        filepath = os.path.join(IPO_DATA_DIR, filename)

        # extract year from filename e.g. ipo-2024.csv → 2024
        year = None
        for part in filename.replace("-", " ").replace("_", " ").split():
            if part.isdigit() and len(part) == 4:
                year = int(part)
                break

        try:
            df = pd.read_csv(filepath)
            df.columns = df.columns.str.strip()

            # standardise column names regardless of source format
            df = df.rename(columns={
                df.columns[0]: "company_name",
                df.columns[1]: "qib_subscription"
            })

            df["qib_subscription"] = pd.to_numeric(
                df["qib_subscription"], errors="coerce"
            )
            df["company_name"] = df["company_name"].astype(str).str.strip()
            df["year"] = year

            # drop rows with no QIB data
            df = df.dropna(subset=["qib_subscription"])

            all_dfs.append(df)
            print(f"  ✓ {filename} — {len(df)} IPOs loaded")

        except Exception as e:
            print(f"  ✗ Could not read {filename}: {e}")

    if not all_dfs:
        print("✗ No data loaded")
        return None

    combined = pd.concat(all_dfs, ignore_index=True)

    # remove duplicates — keep highest QIB if same company appears twice
    combined = combined.sort_values("qib_subscription", ascending=False)
    combined = combined.drop_duplicates(subset=["company_name"], keep="first")
    combined = combined.reset_index(drop=True)

    return combined


def summarise(df):
    print(f"\n=== IPO Lookup Summary ===")
    print(f"Total IPOs: {len(df)}")
    print(f"QIB > 50x  (high bonus): {len(df[df['qib_subscription'] > 50])}")
    print(f"QIB 25-50x (low bonus):  {len(df[(df['qib_subscription'] >= 25) & (df['qib_subscription'] <= 50)])}")
    print(f"QIB < 25x  (no bonus):   {len(df[df['qib_subscription'] < 25])}")
    print(f"\nTop 15 by QIB subscription:")
    print(
        df[["company_name", "qib_subscription", "year"]]
        .sort_values("qib_subscription", ascending=False)
        .head(15)
        .to_string(index=False)
    )


if __name__ == "__main__":
    print("=== IPO Fetcher ===\n")

    os.makedirs("output", exist_ok=True)

    df = load_ipo_csvs()

    if df is not None:
        df.to_csv(OUTPUT_PATH, index=False)
        print(f"\n✓ Saved to {OUTPUT_PATH}")
        summarise(df)
        print("\nIPO fetcher complete — lookup ready for scoring bonus")