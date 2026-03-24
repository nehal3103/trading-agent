from datetime import datetime
from pathlib import Path

import requests
import yfinance as yf
from reliability_utils import cache_read_enveloped, cache_write_enveloped


SECTOR_CACHE_PATH = Path("output/sector_cache.json")
SECTOR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
NSE_HOME_URL = "https://www.nseindia.com"
NSE_QUOTE_URL = "https://www.nseindia.com/api/quote-equity?symbol={symbol}"
NIFTY_500_TICKER = "^CRSLDX"


def _load_cache():
    data, malformed = cache_read_enveloped(
        SECTOR_CACHE_PATH,
        expected_source="sector_strength",
        default_data={}
    )
    if malformed:
        print("  ⚠ Sector cache malformed; rebuilding cache")
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_cache(cache):
    try:
        cache_write_enveloped(SECTOR_CACHE_PATH, "sector_strength", cache)
    except Exception as e:
        print(f"  ⚠ Could not save sector cache: {e}")


def _build_nse_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/get-quotes/equity",
        "Connection": "keep-alive",
    })
    try:
        session.get(NSE_HOME_URL, timeout=10)
    except Exception:
        pass
    return session


def _fetch_sector_from_nse(symbol, session):
    try:
        url = NSE_QUOTE_URL.format(symbol=symbol)
        response = session.get(url, timeout=10)
        if response.status_code != 200:
            return "Unknown"

        data = response.json()
        info = data.get("industryInfo", {}) if isinstance(data, dict) else {}

        sector = (
            info.get("sector")
            or info.get("industry")
            or info.get("basicIndustry")
            or "Unknown"
        )
        sector = str(sector).strip()
        return sector if sector else "Unknown"
    except Exception:
        return "Unknown"


def _fetch_perf_1m(symbol):
    ticker = f"{symbol}.NS"
    perf_1m = 0.0

    try:
        hist = yf.download(
            ticker, period="1mo", interval="1d",
            progress=False, auto_adjust=True
        )
        if not hist.empty:
            close = hist["Close"].dropna().squeeze()
            if hasattr(close, "iloc") and len(close) >= 2:
                first = float(close.iloc[0])
                last = float(close.iloc[-1])
                if first > 0:
                    perf_1m = ((last - first) / first) * 100
    except Exception:
        pass

    return round(perf_1m, 2)


def _fetch_nifty_500_perf_1m():
    perf_1m = 0.0
    try:
        hist = yf.download(
            NIFTY_500_TICKER, period="1mo", interval="1d",
            progress=False, auto_adjust=True
        )
        if not hist.empty:
            close = hist["Close"].dropna().squeeze()
            if hasattr(close, "iloc") and len(close) >= 2:
                first = float(close.iloc[0])
                last = float(close.iloc[-1])
                if first > 0:
                    perf_1m = ((last - first) / first) * 100
    except Exception:
        pass
    return round(perf_1m, 2)


def enrich_with_sector_strength(df):
    """
    Adds:
      - sector
      - stock_perf_1m
      - candidate_sector_avg_1m
      - nifty500_perf_1m
      - candidate_sector_vs_nifty500
      - candidate_sector_strength_label

    Important:
    candidate_sector_avg_1m is based only on the current shortlisted
    candidate set, not the full sector universe.
    """
    if df is None or df.empty or "symbol" not in df.columns:
        return df

    cache = _load_cache()
    today = datetime.now().strftime("%Y-%m-%d")
    nse_session = _build_nse_session()

    sectors = []
    perfs = []

    for symbol in df["symbol"].astype(str).str.upper():
        entry = cache.get(symbol, {})
        cached_date = entry.get("date")
        if cached_date == today:
            sector = entry.get("sector", "Unknown")
            perf_1m = entry.get("perf_1m", 0.0)
        else:
            sector = _fetch_sector_from_nse(symbol, nse_session)
            perf_1m = _fetch_perf_1m(symbol)
            cache[symbol] = {
                "date": today,
                "sector": sector,
                "perf_1m": perf_1m,
            }
        sectors.append(sector)
        perfs.append(perf_1m)

    _save_cache(cache)

    out = df.copy()
    out["sector"] = sectors
    out["stock_perf_1m"] = perfs

    candidate_sector_avg = (
        out.groupby("sector", dropna=False)["stock_perf_1m"]
        .mean()
        .round(2)
        .to_dict()
    )

    out["candidate_sector_avg_1m"] = out["sector"].map(candidate_sector_avg).fillna(0.0)
    nifty500_perf_1m = _fetch_nifty_500_perf_1m()
    out["nifty500_perf_1m"] = nifty500_perf_1m
    out["candidate_sector_vs_nifty500"] = (
        out["candidate_sector_avg_1m"] - nifty500_perf_1m
    ).round(2)

    def strength_label(alpha):
        if alpha >= 2:
            return "Strong vs Nifty500"
        if alpha >= 0:
            return "Slightly Strong vs Nifty500"
        if alpha >= -2:
            return "Slightly Weak vs Nifty500"
        return "Weak vs Nifty500"

    out["candidate_sector_strength_label"] = (
        out["candidate_sector_vs_nifty500"].apply(strength_label)
    )
    return out
