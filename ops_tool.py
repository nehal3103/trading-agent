"""
ops_tool.py  —  Operational entry-point for the trading pipeline.

Usage
─────
Daily (cron / Task Scheduler):
    python3 ops_tool.py daily-news-refresh

Weekly (manual or cron):
    python3 ops_tool.py weekly-pipeline

After uploading new IPO CSV files to ipo_data/:
    python3 ops_tool.py refresh-ipo-lookup

Housekeeping:
    python3 ops_tool.py health-check
    python3 ops_tool.py cleanup-output [--max-files 30]
    python3 ops_tool.py retry-failed-notion [--market-condition GOOD] [--source-csv path]

Cron examples (add to crontab -e)
──────────────────────────────────
# Daily — Mon–Fri at 08:50 IST (03:20 UTC)
20 3 * * 1-5  cd /path/to/project && python3 ops_tool.py daily-news-refresh >> logs/daily.log 2>&1

# Weekly — every Saturday at 09:00 IST (03:30 UTC)
30 3 * * 6    cd /path/to/project && python3 ops_tool.py weekly-pipeline >> logs/weekly.log 2>&1

Windows Task Scheduler equivalent: use 'pythonw ops_tool.py <cmd>' with a .bat wrapper.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── keep .env loaded for all sub-commands ──────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

from ipo_fetcher import OUTPUT_PATH as IPO_OUTPUT_PATH, load_ipo_csvs
from news_analyzer import load_symbols_from_csv, run_news_analysis
from notion_push import retry_failed_from_last_run
from reliability_utils import cache_read_enveloped


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _read_market_condition() -> str:
    try:
        return Path("output/market_condition.txt").read_text().strip()
    except Exception:
        return "UNKNOWN"


def _ensure_output_dirs():
    for d in ["output", "output/nse_cache", "output/ai_cache",
              "output/guidance_cache", "logs"]:
        os.makedirs(d, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND: health-check
# ─────────────────────────────────────────────────────────────────────────────
def health_check() -> int:
    _log("=== Health Check ===")
    checks = []

    # required env vars
    required_env = ["GROQ_API_KEY", "NOTION_TOKEN", "NOTION_DATABASE_ID",
                    "SCREENER_EMAIL", "SCREENER_PASSWORD"]
    for key in required_env:
        val = os.getenv(key)
        checks.append((f"env:{key}", bool(val)))

    # cache files
    cache_checks = [
        ("sector_cache",    "output/sector_cache.json",           "sector_strength"),
        ("gate_meta",       "output/gate_screen_cache.meta.json", "main_gate_screen_meta"),
    ]
    for name, path, source in cache_checks:
        data, malformed = cache_read_enveloped(path, expected_source=source, default_data={})
        ok = (not malformed) and isinstance(data, (dict, list))
        checks.append((f"cache:{name}", ok))

    # tracked symbols exist?
    checks.append((
        "tracked_symbols_csv",
        os.path.exists("output/tracked_symbols.csv")
    ))

    # ipo lookup
    checks.append((
        "ipo_lookup_csv",
        os.path.exists("output/ipo_lookup.csv")
    ))

    all_ok = True
    for name, ok in checks:
        icon = "✓" if ok else "✗"
        print(f"  {icon} {name}")
        if not ok:
            all_ok = False

    if all_ok:
        _log("All checks passed.")
    else:
        _log("Some checks FAILED — review above.")
    return 0 if all_ok else 1


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND: cleanup-output
# ─────────────────────────────────────────────────────────────────────────────
def cleanup_output(max_files: int = 30) -> int:
    """
    Rotates old .log / .tmp / .bak files in output/ into output/archive/.
    Keeps the most recent `max_files` of each pattern; archives the rest.

    Does NOT touch any cache subdirectories:
      - nse_cache/      manages its own 90-day item window on every update
      - ai_cache/       only re-runs when announcement hash changes
      - guidance_cache/ multi-quarter history, never safe to delete
    """
    _log(f"=== Cleanup Output (keep latest {max_files} per pattern) ===")
    out = Path("output")
    if not out.exists():
        _log("No output directory to clean.")
        return 0

    archive_dir = out / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    protected = {
        "run_report.json",
        "notion_last_run.json",
        "final_ranked.csv",
        "tracked_symbols.csv",
        "market_condition.txt",
        "ipo_lookup.csv",
        "gate_screen_cache.csv",
        "gate_screen_cache.meta.json",
    }

    archived = 0
    for pattern in ["*.log", "*.tmp", "*.bak"]:
        files = sorted(out.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files[max_files:]:
            if f.name in protected:
                continue
            f.replace(archive_dir / f.name)
            archived += 1

    _log(f"Done — archived {archived} file(s) to output/archive/")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND: refresh-ipo-lookup
# ─────────────────────────────────────────────────────────────────────────────
def refresh_ipo_lookup(output_path: str = IPO_OUTPUT_PATH) -> int:
    """
    Rebuilds output/ipo_lookup.csv from all CSVs in ipo_data/.
    Run this after uploading new IPO subscription CSV files.
    """
    _log("=== Refresh IPO Lookup ===")
    df = load_ipo_csvs()
    if df is None or df.empty:
        _log("✗ No IPO data available — add CSVs to ipo_data/ and retry.")
        return 1

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    _log(f"✓ IPO lookup refreshed: {out}  ({len(df)} entries)")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND: daily-news-refresh
# ─────────────────────────────────────────────────────────────────────────────
def daily_news_refresh(symbols_csv: str = "output/tracked_symbols.csv") -> int:
    """
    Lightweight daily job — no Screener login, no Notion push.

    What it does:
    1. Reads output/tracked_symbols.csv  (written by weekly-pipeline)
    2. Pulls new NSE announcements for every tracked symbol (incremental)
    3. Runs AI analysis only for symbols with new announcements or no cache
    4. Saves updated AI cache — ready for the next weekly push
    """
    _log("=== Daily News Refresh ===")
    _ensure_output_dirs()

    symbols = load_symbols_from_csv(symbols_csv)
    if not symbols:
        _log(f"✗ No symbols found in {symbols_csv}  — run weekly-pipeline first.")
        return 1

    _log(f"Tracking {len(symbols)} symbols from {symbols_csv}")

    try:
        run_news_analysis(symbols)
    except Exception as e:
        _log(f"✗ News analysis error: {e}")
        return 1

    _log("✓ Daily refresh complete — AI cache updated, no Notion push.")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND: weekly-pipeline
# ─────────────────────────────────────────────────────────────────────────────
def weekly_pipeline() -> int:
    """
    Full weekly run:
    1. Market breadth check
    2. Fetch + cache Screener gate screen
    3. Read Chartink exports
    4. Intersect Chartink ∩ Screener
    5. Score (technical + fundamental)
    6. Sector enrichment + strength
    7. AI news analysis (reuses daily cache where fresh)
    8. Write output/final_ranked.csv
    9. Write output/tracked_symbols.csv  (for daily refresh)
    10. Push to Notion
    """
    _log("=== Weekly Pipeline ===")
    _ensure_output_dirs()

    # import here to avoid circular issues at module level
    from main import run_pipeline

    try:
        report = run_pipeline()
    except Exception as e:
        _log(f"✗ Pipeline raised an exception: {e}")
        import traceback
        traceback.print_exc()
        return 1

    status = report.get("status", "unknown")
    rows   = report.get("rows_final", 0)

    if status == "completed":
        _log(f"✓ Weekly pipeline complete — {rows} companies ranked and pushed to Notion.")
        _write_run_summary(report)
        return 0
    else:
        _log(f"✗ Pipeline finished with status='{status}'. Check output/run_report.json for details.")
        return 1


def _write_run_summary(report: dict):
    """Append a one-line summary to logs/pipeline_history.log."""
    Path("logs").mkdir(exist_ok=True)
    ts      = datetime.now().strftime("%Y-%m-%d %H:%M")
    steps   = report.get("steps", {})
    ok_steps  = [k for k, v in steps.items() if isinstance(v, dict) and v.get("status") == "ok"]
    err_steps = [k for k, v in steps.items() if isinstance(v, dict) and v.get("status") == "error"]
    line = (
        f"{ts} | status={report.get('status')} | rows={report.get('rows_final', 0)} "
        f"| ok={','.join(ok_steps) or 'none'} | err={','.join(err_steps) or 'none'}\n"
    )
    with open("logs/pipeline_history.log", "a") as f:
        f.write(line)


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND: retry-failed-notion
# ─────────────────────────────────────────────────────────────────────────────
def retry_notion(market_condition: str = None,
                 source_csv: str = "output/final_ranked.csv") -> int:
    """
    Re-pushes any symbols that failed in the last Notion push
    (recorded in output/notion_last_run.json).
    """
    _log("=== Retry Failed Notion Rows ===")
    if market_condition is None:
        market_condition = _read_market_condition()

    result = retry_failed_from_last_run(
        market_condition=market_condition,
        source_csv=source_csv
    )

    if result is None:
        return 1
    retried = result.get("created", 0) + result.get("updated", 0)
    failed  = result.get("failed", 0)
    _log(f"Retry done — {retried} pushed, {failed} still failing.")
    return 0 if failed == 0 else 1


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ops_tool",
        description="Trading pipeline operational commands.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # health-check
    sub.add_parser(
        "health-check",
        help="Validate env vars, cache files and directory structure."
    )

    # cleanup-output
    p_clean = sub.add_parser(
        "cleanup-output",
        help="Archive old output files and prune stale per-symbol caches."
    )
    p_clean.add_argument(
        "--max-files", type=int, default=30,
        help="Max .log/.tmp/.bak files to keep in output/ (default: 30)."
    )

    # refresh-ipo-lookup
    sub.add_parser(
        "refresh-ipo-lookup",
        help="Rebuild output/ipo_lookup.csv from ipo_data/*.csv."
    )

    # daily-news-refresh
    p_daily = sub.add_parser(
        "daily-news-refresh",
        help="Update NSE announcement cache + incremental AI. No Notion push."
    )
    p_daily.add_argument(
        "--symbols-csv",
        default="output/tracked_symbols.csv",
        help="CSV with 'symbol' column (default: output/tracked_symbols.csv)."
    )

    # weekly-pipeline
    sub.add_parser(
        "weekly-pipeline",
        help="Run the full pipeline: score, sector, AI, Notion push."
    )

    # retry-failed-notion
    p_retry = sub.add_parser(
        "retry-failed-notion",
        help="Retry Notion rows that failed in the last run."
    )
    p_retry.add_argument(
        "--market-condition", default=None,
        help="Override market condition label (default: read from output/market_condition.txt)."
    )
    p_retry.add_argument(
        "--source-csv", default="output/final_ranked.csv",
        help="Ranked CSV to pull row data from (default: output/final_ranked.csv)."
    )

    return p


def main():
    parser = _build_parser()
    args   = parser.parse_args()

    if args.cmd == "health-check":
        code = health_check()

    elif args.cmd == "cleanup-output":
        code = cleanup_output(max_files=args.max_files)

    elif args.cmd == "refresh-ipo-lookup":
        code = refresh_ipo_lookup()

    elif args.cmd == "daily-news-refresh":
        code = daily_news_refresh(symbols_csv=args.symbols_csv)

    elif args.cmd == "weekly-pipeline":
        code = weekly_pipeline()

    elif args.cmd == "retry-failed-notion":
        code = retry_notion(
            market_condition=args.market_condition,
            source_csv=args.source_csv,
        )

    else:
        parser.print_help()
        code = 1

    raise SystemExit(code)


if __name__ == "__main__":
    main()