import pandas as pd
import numpy as np


# ---------- NORMALIZE ----------
def normalize(series):
    series = pd.to_numeric(series, errors="coerce").fillna(0)

    if series.max() == series.min():
        return pd.Series([0] * len(series), index=series.index)

    return (series - series.min()) / (series.max() - series.min())


# ---------- MATURE SCORING ----------
def score_mature(df):
    df = df.copy()
    df.columns = df.columns.str.strip()

    # ---------- CLEAN ----------
    df = df.replace([np.inf, -np.inf], 0)
    df = df.fillna(0)

    # ---------- QUALITY ----------
    df["roe_score"] = normalize(df["ROE%"])
    df["roce_score"] = normalize(df["ROCE%"])

    # ---------- GROWTH ----------
    df["sales_score"] = normalize(df["Sales growth %"])
    df["profit_score"] = normalize(df["Profit growth %"])

    # Penalize negative growth
    df.loc[df["Sales growth %"] < 0, "sales_score"] *= 0.4
    df.loc[df["Profit growth %"] < 0, "profit_score"] *= 0.4

    # ---------- SAFETY ----------
    df["debt_score"] = 1 - normalize(df["Debt / Eq"])
    df["interest_score"] = normalize(df["Int Coverage"])

    # ---------- CASHFLOW ----------
    df["cfo_eff"] = df["CF Operations Rs.Cr."] / (df["Mar Cap Rs.Cr."] + 1)
    df["cfo_score"] = normalize(df["cfo_eff"])

    # ---------- VALUATION ----------
    df["pe_relative"] = df["P/E"] / (df["Ind PE"] + 1)
    df["pe_score"] = 1 - normalize(df["pe_relative"])

    # ---------- PROMOTER ----------
    df["promoter_score"] = normalize(df["Prom. Hold.%"])

    # ---------- MOMENTUM (PARTIAL CREDIT) ----------
    df["momentum_raw"] = 0

    valid = (
        (df["Sales 2Qtr Bk Rs.Cr."] > 0) &
        (df["Sales 3Qtr Bk Rs.Cr."] > 0)
    )

    df.loc[valid, "momentum_raw"] = (
        (df["Sales Qtr Rs.Cr."] - df["Sales 2Qtr Bk Rs.Cr."]) +
        (df["Sales 2Qtr Bk Rs.Cr."] - df["Sales 3Qtr Bk Rs.Cr."])
    )

    df["momentum_score"] = normalize(df["momentum_raw"])

    # Give partial credit (0.3) for negative growth momentum instead of zeroing out
    df.loc[(df["sales_score"] <= 0.4) | (df["profit_score"] <= 0.4), "momentum_score"] *= 0.3
    
    # kill fake momentum where we don't have data
    df.loc[~valid, "momentum_score"] = 0

    # ---------- SIZE BIAS (NEW) ----------
    df["size_score"] = normalize(df["Mar Cap Rs.Cr."])

    # ---------- FINAL SCORE ----------
    df["final_score"] = (
    # � GROWTH (reduced weight)
    0.10 * df["sales_score"] +
    0.10 * df["profit_score"] +

    # 📈 MOMENTUM (meaningful)
    0.12 * df["momentum_score"] +

    # 🧠 QUALITY (increased - should matter more)
    0.18 * df["roe_score"] +
    0.18 * df["roce_score"] +

    # 🧱 BALANCE SHEET
    0.07 * df["debt_score"] +
    0.05 * df["interest_score"] +

    # 💰 CASH FLOW
    0.08 * df["cfo_score"] +

    # 💸 VALUATION
    0.07 * df["pe_score"] +

    # 👥 PROMOTER
    0.03 * df["promoter_score"]
    )

    # 🎯 FUNDAMENTALS FLOOR - strong companies shouldn't be penalized too much for one bad year
    strong_fundamentals = (df["ROE%"] >= 35) & (df["ROCE%"] >= 35)
    minimum_floor = 0.50
    df.loc[strong_fundamentals, "final_score"] = df.loc[strong_fundamentals, "final_score"].clip(lower=minimum_floor)

    return df.sort_values("final_score", ascending=False)


# ---------- YOUNG/GROWTH SCORING ----------
def score_young(df):
    df = df.copy()
    df.columns = df.columns.str.strip()

    # ---------- CLEAN ----------
    df = df.replace([np.inf, -np.inf], 0)
    df = df.fillna(0)

    # ---------- QUALITY (less critical for young) ----------
    df["roe_score"] = normalize(df["ROE%"])
    df["roce_score"] = normalize(df["ROCE%"])

    # 🚨 DATA QUALITY: Cap unrealistic ROE/ROCE (>100% is suspicious - likely one-off spikes)
    df.loc[df["ROE%"] > 100, "roe_score"] *= 0.4
    df.loc[df["ROCE%"] > 100, "roce_score"] *= 0.4

    # ---------- GROWTH (PRIMARY FOCUS) ----------
    df["sales_score"] = normalize(df["Sales growth %"])
    df["profit_score"] = normalize(df["Profit growth %"])

    # Young companies: softer penalty for any dip (they're still growing)
    # Penalize very negative growth only
    df.loc[df["Sales growth %"] < -10, "sales_score"] *= 0.5
    df.loc[df["Profit growth %"] < -10, "profit_score"] *= 0.5

    # ---------- SAFETY (CRITICAL - avoid overleveraged weak companies) ----------
    df["debt_score"] = 1 - normalize(df["Debt / Eq"])
    df["interest_score"] = normalize(df["Int Coverage"])

    # Strong penalty for high debt young companies
    df.loc[df["Debt / Eq"] > 1.5, "debt_score"] *= 0.3

    # ---------- CASHFLOW ----------
    df["cfo_eff"] = df["CF Operations Rs.Cr."] / (df["Mar Cap Rs.Cr."] + 1)
    df["cfo_score"] = normalize(df["cfo_eff"])

    # ---------- VALUATION ----------
    df["pe_relative"] = df["P/E"] / (df["Ind PE"] + 1)
    df["pe_score"] = 1 - normalize(df["pe_relative"])

    # ---------- PROMOTER ----------
    df["promoter_score"] = normalize(df["Prom. Hold.%"])

    # ---------- MOMENTUM (CRITICAL FOR YOUNG COMPANIES) ----------
    df["momentum_raw"] = 0

    valid = (
        (df["Sales 2Qtr Bk Rs.Cr."] > 0) &
        (df["Sales 3Qtr Bk Rs.Cr."] > 0)
    )

    df.loc[valid, "momentum_raw"] = (
        (df["Sales Qtr Rs.Cr."] - df["Sales 2Qtr Bk Rs.Cr."]) +
        (df["Sales 2Qtr Bk Rs.Cr."] - df["Sales 3Qtr Bk Rs.Cr."])
    )

    df["momentum_score"] = normalize(df["momentum_raw"])
    
    # For young companies, negative momentum is worse - apply stronger penalty
    df.loc[(df["sales_score"] <= 0.3) | (df["profit_score"] <= 0.3), "momentum_score"] *= 0.2
    
    df.loc[~valid, "momentum_score"] = 0

    # ---------- GROWTH CONSISTENCY CHECK (NEW) ----------
    # Flag erratic/spiky growth - penalize companies with extreme variance
    df["qoq_growth_1"] = 0  # Current vs 2Q back
    df["qoq_growth_2"] = 0  # 2Q back vs 3Q back
    
    df.loc[valid, "qoq_growth_1"] = (
        (df.loc[valid, "Sales Qtr Rs.Cr."] - df.loc[valid, "Sales 2Qtr Bk Rs.Cr."]) / 
        (df.loc[valid, "Sales 2Qtr Bk Rs.Cr."] + 1)
    )
    
    df.loc[valid, "qoq_growth_2"] = (
        (df.loc[valid, "Sales 2Qtr Bk Rs.Cr."] - df.loc[valid, "Sales 3Qtr Bk Rs.Cr."]) / 
        (df.loc[valid, "Sales 3Qtr Bk Rs.Cr."] + 1)
    )
    
    # Calculate growth consistency ratio (lower = more erratic)
    # If both QoQ growths have same sign and similar magnitude, consistency is high
    df["growth_consistency"] = 1.0
    
    matching_direction = (df["qoq_growth_1"] > 0) == (df["qoq_growth_2"] > 0)
    df.loc[matching_direction & valid, "growth_consistency"] = 1 - (
        abs(df.loc[matching_direction & valid, "qoq_growth_1"] - 
            df.loc[matching_direction & valid, "qoq_growth_2"]) / 
        (abs(df.loc[matching_direction & valid, "qoq_growth_1"]) + 
         abs(df.loc[matching_direction & valid, "qoq_growth_2"]) + 0.1)
    ).clip(0, 1)
    
    # Highly erratic growth (consistency < 0.3) gets penalized
    erratic = df["growth_consistency"] < 0.3
    df.loc[erratic, "sales_score"] *= 0.6

    # ---------- SIZE BIAS (less relevant for young) ----------
    df["size_score"] = normalize(df["Mar Cap Rs.Cr."])

    # ---------- FINAL SCORE (GROWTH-HEAVY) ----------
    df["final_score"] = (
    # 🚀 GROWTH (PRIMARY - 45% of score)
    0.22 * df["sales_score"] +
    0.23 * df["profit_score"] +

    # 📈 MOMENTUM (HIGH - trajectory matters)
    0.15 * df["momentum_score"] +

    # 🧯 SAFETY (IMPORTANT - avoid toxic debt)
    0.12 * df["debt_score"] +
    0.08 * df["interest_score"] +

    # 🧠 QUALITY (secondary)
    0.08 * df["roe_score"] +
    0.08 * df["roce_score"] +

    # 💰 CASH FLOW (less critical when growing)
    0.03 * df["cfo_score"] +

    # 💸 VALUATION (less focus)
    0.01 * df["pe_score"] +

    # 👥 PROMOTER
    0.02 * df["promoter_score"]
    )

    # 🎯 PROFITABILITY FLOOR - even growth companies should have basic profitability
    low_profitability = df["ROE%"] < 8
    max_score_low_roe = 0.35
    df.loc[low_profitability, "final_score"] = df.loc[low_profitability, "final_score"].clip(upper=max_score_low_roe)

    # 🎯 SAFETY FLOOR - overleveraged young companies get capped
    high_debt = df["Debt / Eq"] > 1.5
    max_score = 0.40
    df.loc[high_debt, "final_score"] = df.loc[high_debt, "final_score"].clip(upper=max_score)

    # 🎖️ GROWTH BONUS - exceptional growth companies get a boost
    exceptional_growth = (df["Sales growth %"] > 30) & (df["Profit growth %"] > 20)
    df.loc[exceptional_growth, "final_score"] *= 1.1

    return df.sort_values("final_score", ascending=False)