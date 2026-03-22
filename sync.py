from notion_client import Client
import os
from openpyxl import load_workbook
import os
import pandas as pd
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN       = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

notion = Client(auth=NOTION_TOKEN)
EXCEL_FILE = "trading_system.xlsx"

# 🧠 Fetch Notion data
def get_rows():
    response = notion.databases.query(database_id=NOTION_DATABASE_ID)
    return response["results"]


# 🧩 Extract fields
def extract(page):
    props = page["properties"]

    # Company
    company = ""
    if props["Company"]["title"]:
        company = props["Company"]["title"][0]["text"]["content"]

    # Entry price (NUMBER field in Notion)
    entry = props["Entry"]["number"]

    return company, entry


# 📊 Check if already exists in Excel
def already_exists(ws, company):
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] == company:
            return True
    return False


# 📊 Append to Excel
def append_excel(company, entry):
    wb = load_workbook(EXCEL_FILE)
    ws = wb.active

    if not already_exists(ws, company):
        ws.append([company, entry])
        print(f"✅ Added: {company}")
    else:
        print(f"⚠️ Skipped (already exists): {company}")

    wb.save(EXCEL_FILE)


# 🚀 Main
def main():
    rows = get_rows()

    for row in rows:
        company, entry = extract(row)

        # 🎯 CORE CONDITION
        if company and entry:
            append_excel(company, entry)


if __name__ == "__main__":
    main()