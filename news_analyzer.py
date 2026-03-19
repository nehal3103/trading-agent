import os
import requests
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# Configure Gemini 2.5 Flash
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash')

def get_realtime_announcements(symbol):
    """Fetches direct 2026 corporate filings from NSE."""
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "accept-language": "en-US,en;q=0.9",
        "referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
    }
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com/companies-listing/corporate-filings-announcements", headers=headers)
        response = session.get(url, headers=headers)
        data = response.json()
        # Filter for 2026 announcements
        ann_2026 = [f"{a['an_dt']}: {a['desc']}" for a in data if "2026" in str(a['an_dt'])]
        return "\n".join(ann_2026) if ann_2026 else "No announcements found for 2026."
    except Exception as e:
        return f"Error fetching 2026 data: {e}"

def analyze_stock_batch(batch_list):
    """Analyzes a group of stocks in one single AI call."""
    combined_data = ""
    for item in batch_list:
        combined_data += f"STOCK: {item['symbol']}\nFILINGS: {item['news']}\n---\n"

    prompt = f"""
    You are a SEBI-focused analyst. Analyze these 2026 NSE filings for the stocks listed below.
    For EACH stock, provide:
    1. 🚩 RED FLAGS: (Management exits, legal issues, or defaults)
    2. 🚀 CATALYSTS: (New orders, expansion, or product launches)
    3. 🎯 VERDICT: (Bullish, Bearish, or Neutral)

    Format exactly like this for each stock:
    SYMBOL: [Your Analysis]

    Data:
    {combined_data}
    """

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Batch Analysis Error: {e}"