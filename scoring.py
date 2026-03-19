import pandas as pd
import numpy as np

try:
    from rapidfuzz import process, fuzz
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    print("⚠ rapidfuzz not installed — IPO bonus will be skipped")


# =========================================================
# NORMALIZATION
# =========================================================
def normalize(series):
    series = pd.to_numeric(series, errors="coerce").fillna(0)
    if series.max() == series.min():
        return pd.Series([0] * len(series), index=series.index)
    return (series - series.min()) / (series.max() - series.min() + 1e-9)


# =========================================================
# IPO BONUS LOOKUP
# =========================================================

# words that are too generic to match on alone
_STOP_WORDS = {
    "ltd", "limited", "ltd.", "pvt", "private", "india", "indian",
    "and", "the", "of", "for", "co", "corp", "corporation",
    "holdings", "holding", "group", "ventures", "solutions",
    "technologies", "technology", "enterprises", "energy",
    "solar", "green", "power", "infra", "infrastructure",
    "pharma", "pharmaceuticals", "capital", "finance", "services"
}


def _first_significant_word(name):
    """
    Extracts the first word from a company name that is
    meaningful enough to identify the company uniquely.
    Skips generic words like Ltd, Solar, Green etc.
    Falls back to the first word if nothing better found.
    """
    words = str(name).lower().replace(".", "").split()
    for word in words:
        if word not in _STOP_WORDS and len(word) >= 4:
            return word
    return words[0] if words else ""


def get_ipo_bonus(company_name, ipo_lookup):
    """
    Two-stage matching:
    Stage 1 — first significant word match
              e.g. 'Waaree Renewab.' → finds 'Waaree Energies Ltd.'
    Stage 2 — fuzzy full name match as fallback (score cutoff 70)

    Bonus tiers:
      QIB > 50x  → +0.10
      QIB 25-50x → +0.05
      no match   → +0.00
    """
    if ipo_lookup is None or not FUZZY_AVAILABLE:
        return 0.0

    ipo_names = ipo_lookup["company_name"].tolist()

    # Stage 1 — first significant word match
    key = _first_significant_word(company_name)
    if key:
        candidates = [
            n for n in ipo_names
            if key in n.lower().replace(".", "")
        ]
        if candidates:
            # among candidates, pick the one with highest fuzzy score
            match = process.extractOne(
                company_name,
                candidates,
                scorer=fuzz.token_sort_ratio
            )
            if match and match[1] >= 40:
                matched_row = ipo_lookup[
                    ipo_lookup["company_name"] == match[0]
                ].iloc[0]
                qib = pd.to_numeric(
                    matched_row.get("qib_subscription", 0),
                    errors="coerce"
                )
                if pd.isna(qib):
                    return 0.0
                if qib > 50:
                    return 0.10
                elif qib >= 25:
                    return 0.05
                else:
                    return 0.00

    # Stage 2 — full fuzzy match fallback
    match = process.extractOne(
        company_name,
        ipo_names,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=70
    )
    if match:
        matched_row = ipo_lookup[
            ipo_lookup["company_name"] == match[0]
        ].iloc[0]
        qib = pd.to_numeric(
            matched_row.get("qib_subscription", 0),
            errors="coerce"
        )
        if pd.isna(qib):
            return 0.0
        if qib > 50:
            return 0.10
        elif qib >= 25:
            return 0.05

    return 0.0


# =========================================================
# MATURE COMPANIES SCORING — Screen 1
# =========================================================
def score_mature(df):
    df = df.copy()
    df.columns = df.columns.str.strip()
    df = df.replace([np.inf, -np.inf], 0).fillna(0)

    # cap outliers before normalizing
    df["Sales growth %"]  = df["Sales growth %"].clip(upper=200)
    df["Profit growth %"] = df["Profit growth %"].clip(upper=300)
    df["ROE%"]            = df["ROE%"].clip(upper=80)
    df["ROCE%"]           = df["ROCE%"].clip(upper=80)
    df["Int Coverage"]    = df["Int Coverage"].clip(upper=50)

    # quality
    df["roe_score"]  = normalize(df["ROE%"])
    df["roce_score"] = normalize(df["ROCE%"])

    # growth — must be positive to score
    df["sales_score"] = np.where(
        df["Sales growth %"] > 0,
        normalize(df["Sales growth %"]),
        0
    )
    df["profit_score"] = np.where(
        df["Profit growth %"] > 0,
        normalize(df["Profit growth %"]),
        0
    )

    # safety
    df["debt_score"]     = 1 - normalize(df["Debt / Eq"])
    df["interest_score"] = normalize(df["Int Coverage"])

    # cashflow vs market cap
    df["cfo_eff"]   = df["CF Operations Rs.Cr."] / (df["Mar Cap Rs.Cr."].replace(0, np.nan))
    df["cfo_score"] = normalize(df["cfo_eff"].fillna(0))

    # valuation
    df["pe_relative"] = df["P/E"] / (df["Ind PE"] + 1)
    df["pe_score"] = np.where(
        df["pe_relative"] < 1,
        1 - df["pe_relative"],
        0
    )

    # promoter
    df["promoter_score"] = normalize(df["Prom. Hold.%"])

    # momentum — 3 quarter sales trend
    valid = (
        (df["Sales 2Qtr Bk Rs.Cr."] > 0) &
        (df["Sales 3Qtr Bk Rs.Cr."] > 0)
    )
    df["momentum_raw"] = np.where(
        valid,
        (
            (df["Sales Qtr Rs.Cr."] - df["Sales 2Qtr Bk Rs.Cr."]) /
            (df["Sales 2Qtr Bk Rs.Cr."] + 1)
        ) + (
            (df["Sales 2Qtr Bk Rs.Cr."] - df["Sales 3Qtr Bk Rs.Cr."]) /
            (df["Sales 3Qtr Bk Rs.Cr."] + 1)
        ),
        0.0
    )
    df["momentum_raw"]   = df["momentum_raw"].clip(-2, 2)
    df["momentum_score"] = normalize(df["momentum_raw"])

    # momentum only counts if underlying growth exists
    df["momentum_score"] = np.where(
        (df["sales_score"] > 0.3) & (df["profit_score"] > 0.3),
        df["momentum_score"],
        0
    )

    # final score
    df["final_score"] = (
        0.18 * df["sales_score"] +
        0.18 * df["profit_score"] +
        0.16 * df["momentum_score"] +
        0.12 * df["roe_score"] +
        0.12 * df["roce_score"] +
        0.05 * df["debt_score"] +
        0.05 * df["interest_score"] +
        0.07 * df["cfo_score"] +
        0.04 * df["pe_score"] +
        0.03 * df["promoter_score"]
    )

    # shrinking companies hard cap at 0.30
    shrinking = (
        (df["Sales growth %"] < 0) &
        (df["Profit growth %"] < 0)
    )
    df.loc[shrinking, "final_score"] = df.loc[shrinking, "final_score"].clip(upper=0.30)
    df["flag"] = ""
    df.loc[shrinking, "flag"] = "SHRINKING"
    df["ipo_bonus"] = 0.0

    return df.sort_values("final_score", ascending=False)


# =========================================================
# MIDCAP GROWTH COMPANIES SCORING — Screen 2
# =========================================================
def score_midcap(df, ipo_lookup=None):
    df = df.copy()
    df.columns = df.columns.str.strip()
    df = df.replace([np.inf, -np.inf], 0).fillna(0)

    # drop companies with essentially zero cash flow
    df = df[df["CF Operations Rs.Cr."] > 1].copy()

    # cap outliers
    df["Sales growth %"]  = df["Sales growth %"].clip(upper=200)
    df["Profit growth %"] = df["Profit growth %"].clip(upper=300)
    df["ROE%"]            = df["ROE%"].clip(upper=80)
    df["ROCE%"]           = df["ROCE%"].clip(upper=80)
    df["Int Coverage"]    = df["Int Coverage"].clip(upper=50)

    # growth
    df["sales_score"] = np.where(
        df["Sales growth %"] > 0,
        normalize(df["Sales growth %"]),
        0
    )
    df["profit_score"] = np.where(
        df["Profit growth %"] > 0,
        normalize(df["Profit growth %"]),
        0
    )

    # quality
    df["roe_score"]  = normalize(df["ROE%"])
    df["roce_score"] = normalize(df["ROCE%"])

    # safety
    df["debt_score"] = 1 - normalize(df["Debt / Eq"])

    # momentum
    valid = (
        (df["Sales 2Qtr Bk Rs.Cr."] > 0) &
        (df["Sales 3Qtr Bk Rs.Cr."] > 0)
    )
    df["momentum_raw"] = np.where(
        valid,
        (
            (df["Sales Qtr Rs.Cr."] - df["Sales 2Qtr Bk Rs.Cr."]) /
            (df["Sales 2Qtr Bk Rs.Cr."] + 1)
        ) + (
            (df["Sales 2Qtr Bk Rs.Cr."] - df["Sales 3Qtr Bk Rs.Cr."]) /
            (df["Sales 3Qtr Bk Rs.Cr."] + 1)
        ),
        0.0
    )
    df["momentum_raw"]   = df["momentum_raw"].clip(-2, 2)
    df["momentum_score"] = normalize(df["momentum_raw"])

    # zero out momentum where historical data missing
    df["momentum_score"] = np.where(valid, df["momentum_score"], 0)

    # zero out momentum for data anomalies
    # current quarter 10x previous = not organic growth
    df["momentum_score"] = np.where(
        df["Sales Qtr Rs.Cr."] > df["Sales 2Qtr Bk Rs.Cr."] * 10,
        0,
        df["momentum_score"]
    )

    # promoter
    df["promoter_score"] = normalize(df["Prom. Hold.%"])

    # base final score
    df["final_score"] = (
        0.22 * df["sales_score"] +
        0.22 * df["profit_score"] +
        0.18 * df["momentum_score"] +
        0.14 * df["roe_score"] +
        0.10 * df["roce_score"] +
        0.08 * df["debt_score"] +
        0.06 * df["promoter_score"]
    )

    # shrinking companies hard cap
    shrinking = (
        (df["Sales growth %"] < 0) &
        (df["Profit growth %"] < 0)
    )
    df.loc[shrinking, "final_score"] = df.loc[shrinking, "final_score"].clip(upper=0.30)
    df["flag"] = ""
    df.loc[shrinking, "flag"] = "SHRINKING"

    # IPO bonus
    df["ipo_bonus"] = df["Name"].apply(
        lambda name: get_ipo_bonus(name, ipo_lookup)
    )
    df["final_score"] = (df["final_score"] + df["ipo_bonus"]).clip(upper=1.0)

    df.loc[df["ipo_bonus"] > 0, "flag"] = df.loc[df["ipo_bonus"] > 0, "flag"].apply(
        lambda f: "IPO_BONUS | " + f if f else "IPO_BONUS"
    )

    return df.sort_values("final_score", ascending=False)