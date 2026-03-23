import argparse
import os

import pandas as pd

from sector_strength import enrich_with_sector_strength
from notion_push import push_to_notion


def build_parser():
    parser = argparse.ArgumentParser(
        description="Test sector_strength and notion_push modules only."
    )
    parser.add_argument(
        "--input",
        default="output/final_ranked.csv",
        help="Input ranked CSV path (default: output/final_ranked.csv)",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=5,
        help="How many top rows to test (default: 5)",
    )
    parser.add_argument(
        "--push-notion",
        action="store_true",
        help="Push tested rows to Notion after enrichment",
    )
    parser.add_argument(
        "--market-condition",
        default="TEST",
        help="Market condition label used for Notion test push",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"✗ Input file not found: {args.input}")
        return

    df = pd.read_csv(args.input)
    if df.empty:
        print("✗ Input CSV has no rows.")
        return

    test_df = df.head(max(args.rows, 1)).copy()
    print(f"Loaded {len(test_df)} rows for module test")

    enriched = enrich_with_sector_strength(test_df)
    preview_cols = [
        "symbol",
        "sector",
        "stock_perf_1m",
        "sector_strength",
        "nifty500_perf_1m",
        "sector_vs_nifty500",
        "sector_strength_label",
    ]
    preview_cols = [c for c in preview_cols if c in enriched.columns]
    print("\n=== Sector Module Preview ===")
    print(enriched[preview_cols].to_string(index=False))

    os.makedirs("output", exist_ok=True)
    out_path = "output/final_ranked_sector_test.csv"
    enriched.to_csv(out_path, index=False)
    print(f"\n✓ Saved enriched test output: {out_path}")

    if args.push_notion:
        print("\n=== Notion Module Test Push ===")
        push_to_notion(
            market_condition=args.market_condition,
            ranked_df=enriched
        )
    else:
        print("\n(Notion push skipped. Use --push-notion to enable.)")


if __name__ == "__main__":
    main()
