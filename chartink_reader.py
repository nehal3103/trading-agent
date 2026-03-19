import os
import pandas as pd
from config import CHARTINK_DIR, MIN_SCANNER_COUNT

SCANNERS = {
    "stage2":         "stage2.csv",
    "growthstocks":   "growthstocks.csv",
    "buying_urgency": "buying_urgency.csv",
}

def load_scanner(filepath):
    df = pd.read_csv(filepath)
    df.columns = df.columns.str.strip()
    rename_map = {}
    for col in df.columns:
        col_lower = col.lower().strip()
        if "stock" in col_lower or "name" in col_lower:
            rename_map[col] = "stock_name"
        elif "symbol" in col_lower:
            rename_map[col] = "symbol"
        elif col_lower == "close":
            rename_map[col] = "close"
        elif "%" in col_lower or "change" in col_lower:
            rename_map[col] = "pct_change"
        elif "volume" in col_lower:
            rename_map[col] = "volume"
    df = df.rename(columns=rename_map)
    df["symbol"]     = df["symbol"].astype(str).str.strip()
    df["stock_name"] = df["stock_name"].astype(str).str.strip()
    return df[["symbol", "stock_name", "close"]].copy()

def read_chartink_exports():
    if not os.path.exists(CHARTINK_DIR):
        print(f"✗ Folder '{CHARTINK_DIR}' not found")
        print(f"  Create it and put stage2.csv, growthstocks.csv, buying_urgency.csv inside")
        return None

    loaded = {}
    for name, filename in SCANNERS.items():
        filepath = os.path.join(CHARTINK_DIR, filename)
        if not os.path.exists(filepath):
            print(f"  ⚠ {filename} not found — skipping")
            continue
        df = load_scanner(filepath)
        loaded[name] = df
        print(f"  ✓ {name:20} — {len(df)} stocks loaded")

    if not loaded:
        print("✗ No scanner files found")
        return None

    symbol_counts = {}
    symbol_names  = {}
    symbol_close  = {}
    symbol_flags  = {}

    for scanner_name, df in loaded.items():
        for _, row in df.iterrows():
            sym = row["symbol"]
            if sym not in symbol_counts:
                symbol_counts[sym] = 0
                symbol_names[sym]  = row["stock_name"]
                symbol_close[sym]  = row["close"]
                symbol_flags[sym]  = []
            symbol_counts[sym] += 1
            symbol_flags[sym].append(scanner_name)

    rows = []
    for sym, count in symbol_counts.items():
        if count >= 1:  # show all stocks, even those in just 1 scanner
            scanners_str = " + ".join(symbol_flags[sym])
            if count == 3:
                priority = "A — all three scanners"
            elif "stage2" in symbol_flags[sym] and "buying_urgency" in symbol_flags[sym]:
                priority = "A — stage2 + institutions"
            elif "stage2" in symbol_flags[sym] and "growthstocks" in symbol_flags[sym]:
                priority = "B — stage2 + base"
            elif "buying_urgency" in symbol_flags[sym] and "growthstocks" in symbol_flags[sym]:
                priority = "B — institutions + base"
            else:
                priority = "C — other combination"
            rows.append({
                "symbol":        sym,
                "stock_name":    symbol_names[sym],
                "close":         symbol_close[sym],
                "scanner_count": count,
                "scanners":      scanners_str,
                "priority":      priority,
            })

    if not rows:
        print(f"\n  No stocks found in {MIN_SCANNER_COUNT}+ scanners")
        return None

    result = pd.DataFrame(rows)
    result = result.sort_values(
        ["scanner_count", "priority"],
        ascending=[False, True]
    ).reset_index(drop=True)
    return result

def print_summary(df):
    print(f"Total stocks in 2+ scanners: {len(df)}")
    print()
    for priority in df["priority"].unique():
        subset = df[df["priority"] == priority]
        print(f"  {priority} ({len(subset)} stocks):")
        for _, row in subset.iterrows():
            print(f"    {row['symbol']:15} {row['stock_name']:45} ₹{row['close']}")
        print()
