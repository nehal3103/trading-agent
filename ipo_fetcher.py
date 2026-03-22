import pandas as pd
import os

IPO_DATA_DIR = "ipo_data"
OUTPUT_PATH  = "output/ipo_lookup.csv"


def load_ipo_csvs():
    if not os.path.exists(IPO_DATA_DIR):
        print(f"✗ Folder '{IPO_DATA_DIR}' not found")
        return None

    files = [f for f in os.listdir(IPO_DATA_DIR) if f.endswith(".csv")]
    if not files:
        print(f"✗ No CSV files found in '{IPO_DATA_DIR}/'")
        return None

    all_dfs = []

    for filename in sorted(files):
        filepath = os.path.join(IPO_DATA_DIR, filename)

        try:
            df = pd.read_csv(filepath)
            df.columns = df.columns.str.strip()

            # standardise first two columns
            df = df.rename(columns={
                df.columns[0]: "company_name",
                df.columns[1]: "qib_subscription"
            })

            df["qib_subscription"] = pd.to_numeric(
                df["qib_subscription"], errors="coerce"
            )
            df["company_name"] = df["company_name"].astype(str).str.strip()

            # use Year column if present (manually added)
            # otherwise extract from filename
            if "Year" in df.columns:
                df["year"] = pd.to_numeric(df["Year"], errors="coerce")
                df = df.drop(columns=["Year"])
            else:
                # extract year from filename — strip .csv first
                year = None
                name_clean = filename.replace(".csv", "").replace("-", " ").replace("_", " ")
                for part in name_clean.split():
                    if part.isdigit() and len(part) == 4:
                        year = int(part)
                        break
                df["year"] = year

            df = df.dropna(subset=["qib_subscription"])
            all_dfs.append(df)
            print(f"  ✓ {filename} — {len(df)} IPOs loaded")

        except Exception as e:
            print(f"  ✗ Could not read {filename}: {e}")

    if not all_dfs:
        return None

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.sort_values("qib_subscription", ascending=False)
    combined = combined.drop_duplicates(subset=["company_name"], keep="first")
    combined = combined.reset_index(drop=True)
    return combined


def summarise(df):
    print(f"\n=== IPO Lookup Summary ===")
    print(f"Total IPOs: {len(df)}")
    print(f"QIB > 50x  : {len(df[df['qib_subscription'] > 50])}")
    print(f"QIB 25-50x : {len(df[(df['qib_subscription'] >= 25) & (df['qib_subscription'] <= 50)])}")
    print(f"QIB < 25x  : {len(df[df['qib_subscription'] < 25])}")
    print(f"\nTop 15 by QIB:")
    print(df[["company_name", "qib_subscription", "year"]]
          .sort_values("qib_subscription", ascending=False)
          .head(15).to_string(index=False))


if __name__ == "__main__":
    print("=== IPO Fetcher ===\n")
    os.makedirs("output", exist_ok=True)
    df = load_ipo_csvs()
    if df is not None:
        df.to_csv(OUTPUT_PATH, index=False)
        print(f"\n✓ Saved to {OUTPUT_PATH}")
        summarise(df)