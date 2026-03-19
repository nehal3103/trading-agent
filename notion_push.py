import os
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN       = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

notion = Client(auth=NOTION_TOKEN)


def safe_float(val):
    try:
        return round(float(val), 4)
    except:
        return 0.0


def safe_str(val):
    if pd.isna(val):
        return ""
    return str(val).strip()


def build_summary(row):
    final_score  = safe_float(row.get("final_score", 0))
    tech_score   = safe_float(row.get("tech_score", 0))
    funda_score  = safe_float(row.get("funda_score", 0))
    scanners     = safe_str(row.get("scanners", ""))
    priority     = safe_str(row.get("priority", ""))
    roe          = safe_str(row.get("ROE%", ""))
    roce         = safe_str(row.get("ROCE%", ""))
    debt_eq      = safe_str(row.get("Debt / Eq", ""))
    promoter     = safe_str(row.get("Prom. Hold.%", ""))
    close        = safe_str(row.get("close", ""))
    ipo_status   = safe_str(row.get("ipo_status", ""))
    # --- New AI Narrative Field ---
    ai_narrative = safe_str(row.get("ai_narrative", "No AI analysis available."))

    lines = []
    
    # 1. IPO Status (if applicable)
    if ipo_status:
        lines.append(f"*** {ipo_status.upper()} ***")
        lines.append("")

    # 2. AI Narrative (Red Flags & Catalysts)
    lines.append("--- AI RESEARCH NARRATIVE ---")
    lines.append(ai_narrative)
    lines.append("----------------------------")
    lines.append("")

    # 3. Quantitative Data
    lines.extend([
        f"Final score: {round(final_score * 100, 1)}%",
        f"Technical: {round(tech_score * 100, 1)}% | Fundamental: {round(funda_score * 100, 1)}%",
        f"",
        f"Scanners: {scanners}",
        f"Priority: {priority}",
        f"",
        f"CMP: Rs.{close}",
        f"ROE: {roe}% | ROCE: {roce}%",
        f"D/E: {debt_eq} | Promoter: {promoter}%",
    ])
    return "\n".join(lines)


def push_company(row, scan_date):
    name    = safe_str(row.get("stock_name", "Unknown"))
    symbol  = safe_str(row.get("symbol", ""))
    score   = safe_float(row.get("final_score", 0))
    market  = safe_str(row.get("market_condition", "UNKNOWN"))
    ipo_tag = safe_str(row.get("ipo_status", ""))
    
    title   = f"{symbol} — {name}"
    summary = build_summary(row)
    priority = safe_str(row.get("priority", "—"))[:100]

    # Status logic
    status_val = "High QIB IPO" if ipo_tag else "To Review"

    # check if already exists
    existing = notion.databases.query(
        database_id=NOTION_DATABASE_ID,
        filter={
            "property": "Company",
            "title": {"equals": title}
        }
    )

    # Note: Notion rich_text has a 2000 character limit per block.
    # We truncate the summary to ensure the API call doesn't fail.
    properties = {
        "Company": {
            "title": [{"text": {"content": title}}]
        },
        "Screen": {
            "select": {"name": priority}
        },
        "Score": {
            "number": round(score * 100, 1)
        },
        "Market Condition": {
            "select": {"name": market}
        },
        "AI Summary": {
            "rich_text": [{"text": {"content": summary[:2000]}}]
        },
        "Status": {
            "select": {"name": status_val}
        },
        "Scan Date": {
            "date": {"start": scan_date}
        }
    }

    if existing["results"]:
        page_id = existing["results"][0]["id"]
        notion.pages.update(page_id=page_id, properties=properties)
        return "updated"
    else:
        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=properties
        )
        return "created"


def push_to_notion(market_condition="UNKNOWN", ranked_df=None):
    print("=== Notion Push with AI Narrative ===\n")
    scan_date = datetime.today().strftime("%Y-%m-%d")

    if ranked_df is None:
        path = "output/final_ranked.csv"
        if not os.path.exists(path):
            print(f"✗ {path} not found — run main.py first")
            return
        ranked_df = pd.read_csv(path)

    total_created = 0
    total_updated = 0
    total_failed  = 0

    for _, row in ranked_df.iterrows():
        symbol = safe_str(row.get("symbol", "Unknown"))
        try:
            result = push_company(row, scan_date)
            icon = "✓" if result == "created" else "↺"
            if result == "created":
                total_created += 1
            else:
                total_updated += 1
            score = safe_float(row.get("final_score", 0))
            print(f"  {icon} {symbol:12} score: {round(score*100,1)}%")
        except Exception as e:
            print(f"  ✗ {symbol} — {e}")
            total_failed += 1

    print(f"\nCreated: {total_created} | Updated: {total_updated} | Failed: {total_failed}")
    print(f"✓ Open Notion to see AI summaries.")


if __name__ == "__main__":
    market_condition = "UNKNOWN"
    try:
        with open("output/market_condition.txt", "r") as f:
            market_condition = f.read().strip()
    except:
        pass
    push_to_notion(market_condition=market_condition)