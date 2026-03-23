import argparse
import os
from pathlib import Path

import pandas as pd

from ipo_fetcher import OUTPUT_PATH, load_ipo_csvs
from main import run_pipeline
from news_analyzer import load_symbols_from_csv, run_news_analysis
from notion_push import retry_failed_from_last_run
from reliability_utils import cache_read_enveloped


def health_check():
    checks = []
    required_env = ["GROQ_API_KEY", "NOTION_TOKEN", "NOTION_DATABASE_ID"]
    for key in required_env:
        checks.append((f"env:{key}", bool(os.getenv(key))))

    cache_files = [
        ("sector_cache", "output/sector_cache.json", "sector_strength"),
        ("gate_meta", "output/gate_screen_cache.meta.json", "main_gate_screen_meta"),
    ]
    for name, path, source in cache_files:
        data, malformed = cache_read_enveloped(path, expected_source=source, default_data={})
        ok = (not malformed) and isinstance(data, (dict, list))
        checks.append((f"cache:{name}", ok))

    all_ok = True
    print("=== Health Check ===")
    for name, ok in checks:
        icon = "✓" if ok else "✗"
        print(f"  {icon} {name}")
        if not ok:
            all_ok = False
    return 0 if all_ok else 1


def cleanup_output(max_files=30):
    out = Path("output")
    if not out.exists():
        print("No output directory to clean.")
        return

    archived = 0
    deleted = 0
    archive_dir = out / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    patterns = ["*.log", "*.tmp", "*.bak", "*.json"]
    for pattern in patterns:
        files = sorted(out.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        keep = files[:max_files]
        remove = files[max_files:]
        keep_set = {p.resolve() for p in keep}
        for f in remove:
            if f.resolve() in keep_set:
                continue
            if f.name in {"run_report.json", "notion_last_run.json"}:
                continue
            target = archive_dir / f.name
            f.replace(target)
            archived += 1

    # Delete stale cached pyc builds in output/.pycache
    pycache = out / ".pycache"
    if pycache.exists():
        for pyc in pycache.glob("**/*.pyc"):
            try:
                pyc.unlink()
                deleted += 1
            except Exception:
                pass

    print(f"Cleanup complete: archived={archived}, deleted={deleted}")


def refresh_ipo_lookup(output_path=OUTPUT_PATH):
    df = load_ipo_csvs()
    if df is None or df.empty:
        print("✗ No IPO data available to build lookup.")
        return 1

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"✓ IPO lookup refreshed: {out}")
    return 0


def daily_news_refresh(symbols_csv="output/tracked_symbols.csv"):
    symbols = load_symbols_from_csv(symbols_csv)
    if not symbols:
        return 1

    run_news_analysis(symbols)
    return 0


def weekly_pipeline():
    report = run_pipeline()
    return 0 if report.get("status") == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description="Operational reliability helper commands.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_health = sub.add_parser("health-check", help="Validate env vars and cache readability")
    p_health.set_defaults(func=lambda args: health_check())

    p_clean = sub.add_parser("cleanup-output", help="Rotate/archive bulky output artifacts")
    p_clean.add_argument("--max-files", type=int, default=30, help="Files to keep per pattern")
    p_clean.set_defaults(func=lambda args: cleanup_output(max_files=args.max_files) or 0)

    p_retry = sub.add_parser("retry-failed-notion", help="Retry failed Notion rows from last run report")
    p_retry.add_argument("--market-condition", default="UNKNOWN")
    p_retry.add_argument("--source-csv", default="output/final_ranked.csv")
    p_retry.set_defaults(
        func=lambda args: (
            retry_failed_from_last_run(
                market_condition=args.market_condition,
                source_csv=args.source_csv
            ),
            0
        )[1]
    )

    p_news = sub.add_parser(
        "daily-news-refresh",
        help="Update daily NSE announcement cache and incremental AI for tracked symbols",
    )
    p_news.add_argument(
        "--symbols-csv",
        default="output/tracked_symbols.csv",
        help="CSV containing tracked symbols from the latest weekly run",
    )
    p_news.set_defaults(func=lambda args: daily_news_refresh(symbols_csv=args.symbols_csv))

    p_ipo = sub.add_parser(
        "refresh-ipo-lookup",
        help="Rebuild output/ipo_lookup.csv from ipo_data/*.csv",
    )
    p_ipo.set_defaults(func=lambda args: refresh_ipo_lookup())

    p_weekly = sub.add_parser(
        "weekly-pipeline",
        help="Run the full weekly trading pipeline",
    )
    p_weekly.set_defaults(func=lambda args: weekly_pipeline())

    args = parser.parse_args()
    code = args.func(args)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
