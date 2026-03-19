import yfinance as yf
import pandas as pd
import numpy as np

GATE_FAIL_THRESHOLD = 2


def get_nifty_trend():
    try:
        df = yf.download("^NSEI", period="6mo", interval="1d", progress=False)
        if df.empty:
            return None, "Could not fetch Nifty 50 data"
        close = df["Close"].squeeze()
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        price = float(close.iloc[-1])
        e20   = float(ema20.iloc[-1])
        e50   = float(ema50.iloc[-1])
        above_ema20 = price > e20
        above_ema50 = price > e50
        if above_ema20 and above_ema50:
            status, healthy = "BULLISH", True
        elif above_ema20 or above_ema50:
            status, healthy = "NEUTRAL", True
        else:
            status, healthy = "BEARISH", False
        detail = f"Nifty: {price:.0f} | 20 EMA: {e20:.0f} | 50 EMA: {e50:.0f} | {status}"
        return healthy, detail
    except Exception as e:
        return None, f"Error: {e}"


def get_india_vix():
    try:
        df = yf.download("^INDIAVIX", period="5d", interval="1d", progress=False)
        if df.empty:
            return None, "Could not fetch India VIX"
        vix = float(df["Close"].squeeze().iloc[-1])
        if vix < 15:
            status, healthy = "LOW — calm market", True
        elif vix <= 20:
            status, healthy = "MODERATE — acceptable", True
        else:
            status, healthy = "HIGH — risky for swing trades", False
        return healthy, f"India VIX: {vix:.2f} | {status}"
    except Exception as e:
        return None, f"Error: {e}"


def get_advance_decline():
    try:
        df500 = yf.download("^CRSLDX", period="1y", interval="1d", progress=False)
        if df500.empty:
            df500 = yf.download("^CNX200", period="1y", interval="1d", progress=False)
        if df500.empty:
            return None, "Could not fetch breadth data"
        close   = df500["Close"].squeeze()
        ma200   = close.rolling(window=200).mean()
        price   = float(close.iloc[-1])
        ma200_v = float(ma200.iloc[-1])
        pct     = ((price - ma200_v) / ma200_v) * 100
        above   = price > ma200_v
        if above and pct > 5:
            status, healthy = "STRONG — well above 200 DMA", True
        elif above:
            status, healthy = "NEUTRAL — just above 200 DMA", True
        else:
            status, healthy = "WEAK — below 200 DMA", False
        detail = f"Nifty 500 proxy: {price:.0f} | 200 DMA: {ma200_v:.0f} | {pct:+.1f}% | {status}"
        return healthy, detail
    except Exception as e:
        return None, f"Error: {e}"


def get_nifty_momentum():
    try:
        df = yf.download("^NSEI", period="3mo", interval="1d", progress=False)
        if df.empty:
            return None, "Could not fetch momentum data"
        close      = df["Close"].squeeze()
        recent_5   = float(close.iloc[-5:].mean())
        previous_5 = float(close.iloc[-10:-5].mean())
        momentum_pct = ((recent_5 - previous_5) / previous_5) * 100
        if momentum_pct > 1:
            status, healthy = "POSITIVE", True
        elif momentum_pct >= -1:
            status, healthy = "FLAT", True
        else:
            status, healthy = "NEGATIVE", False
        detail = f"5d avg: {recent_5:.0f} vs prev 5d: {previous_5:.0f} | {momentum_pct:+.2f}% | {status}"
        return healthy, detail
    except Exception as e:
        return None, f"Error: {e}"


def check_market_breadth():
    """
    Runs all 4 signals and returns gate status.
    Gate NEVER halts the pipeline — it only sets the market condition label.
    Labels: GOOD / CAUTION / RISKY
    """
    print("=== Market Breadth Gate ===\n")

    signals = {
        "Nifty trend (EMA)":   get_nifty_trend,
        "India VIX":           get_india_vix,
        "Market breadth":      get_advance_decline,
        "Short term momentum": get_nifty_momentum,
    }

    results    = {}
    fail_count = 0

    for name, fn in signals.items():
        healthy, detail = fn()
        results[name] = {"healthy": healthy, "detail": detail}

        if healthy is None:
            status_str = "⚠ ERROR"
        elif healthy:
            status_str = "✓ PASS"
        else:
            status_str = "✗ FAIL"
            fail_count += 1

        print(f"  {status_str}  {name}")
        print(f"         {detail}\n")

    # determine market condition label
    if fail_count == 0:
        market_condition = "GOOD"
        label = "✓ Market looks healthy — normal risk"
    elif fail_count < GATE_FAIL_THRESHOLD:
        market_condition = "CAUTION"
        label = "⚠ Mixed signals — trade with reduced size"
    else:
        market_condition = "RISKY"
        label = "✗ Market is weak — trade at your own risk"

    gate_passes = fail_count < GATE_FAIL_THRESHOLD

    print("─" * 55)
    print(f"  Signals failed : {fail_count} / {len(signals)}")
    print(f"  Market condition : {market_condition}")
    print(f"  {label}")
    print("─" * 55)

    # pipeline always continues — gate is advisory only
    print("\n  Pipeline will continue regardless.")
    print("  Market condition will be tagged on each company in Notion.\n")

    return gate_passes, market_condition, results


if __name__ == "__main__":
    gate_passes, market_condition, results = check_market_breadth()