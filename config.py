BASE_URL = "https://www.screener.in"

SCREEN_1 = {
    "id": "3550710",
    "slug": "mature-companies"
}

SCREEN_2 = {
    "id": "3550732",
    "slug": "young-companies"
}

# fundamental gate screen for Chartink-based pipeline
GATE_SCREEN = {
    "id":   "3556661",
    "slug": "technical-funda-gate"
}

# chartink exports folder
CHARTINK_DIR = "chartink_exports"

# minimum scanners a stock must appear in
MIN_SCANNER_COUNT = 2

# scoring weights
WEIGHT_TECHNICAL   = 0.55
WEIGHT_FUNDAMENTAL = 0.45