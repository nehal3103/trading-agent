import pandas as pd


def clean_dataframe(df):
    # normalize column names
    df.columns = df.columns.str.replace("Rs.Cr.", " Rs.Cr.", regex=False)
    df.columns = df.columns.str.replace("growth%", "growth %", regex=False)
    df.columns = df.columns.str.strip()

    # remove duplicate columns
    df = df.loc[:, ~df.columns.duplicated()]

    columns_to_clean = [
        "ROE%", "ROCE%", "Debt / Eq", "P/E",
        "Prom. Hold.%", "Int Coverage",
        "Mar Cap Rs.Cr.", "CF Operations Rs.Cr.",
        "Sales growth %", "Profit growth %",
        "Sales Qtr Rs.Cr.", "Sales 2Qtr Bk Rs.Cr.", "Sales 3Qtr Bk Rs.Cr."
    ]

    for col in columns_to_clean:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(",", "")
                .str.replace("%", "")
                .str.strip()
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "Name" in df.columns:
        df["Name"] = df["Name"].astype(str).str.strip()

    return df