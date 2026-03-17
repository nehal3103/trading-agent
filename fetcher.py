import requests
import pandas as pd
from bs4 import BeautifulSoup
from config import BASE_URL


# ---------------------------
# LOGIN
# ---------------------------
def get_session(email, password):
    session = requests.Session()

    login_page = session.get(f"{BASE_URL}/login/")
    soup = BeautifulSoup(login_page.content, "html.parser")
    csrf = soup.find("input", {"name": "csrfmiddlewaretoken"})["value"]

    payload = {
        "username": email,
        "password": password,
        "csrfmiddlewaretoken": csrf,
    }

    headers = {
        "Referer": f"{BASE_URL}/login/",
        "User-Agent": "Mozilla/5.0"
    }

    response = session.post(f"{BASE_URL}/login/", data=payload, headers=headers)

    if "logout" in response.text.lower():
        print("✓ Logged into Screener")
        return session
    else:
        print("✗ Login failed")
        return None


# ---------------------------
# FETCH SCREEN
# ---------------------------
def fetch_screen(session, screen_id, screen_slug):
    print(f"\n--- Fetching: {screen_slug} ---")

    all_rows = []
    headers = None
    page = 1
    existing_names = set()  # ⭐ key fix

    while True:
        url = f"{BASE_URL}/screens/{screen_id}/{screen_slug}/?page={page}"
        res = session.get(url)

        soup = BeautifulSoup(res.content, "html.parser")
        table = soup.find("table")

        if not table:
            print(f"✓ Stopping at page {page}")
            break

        # headers (once)
        if headers is None:
            raw_headers = [th.get_text(strip=True) for th in table.find_all("th")]

            seen = set()
            headers = []
            for h in raw_headers:
                if h not in seen:
                    headers.append(h)
                    seen.add(h)

            print(f"Columns: {headers}")

        rows = table.find_all("tr")[1:]
        page_data = []

        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue

            row_data = []

            for i, cell in enumerate(cells):
                if i == 1:  # Name column
                    a = cell.find("a")
                    text = a.get_text(strip=True) if a else cell.get_text(strip=True)
                else:
                    text = cell.get_text(strip=True)

                row_data.append(text)

            # normalize length
            if len(row_data) < len(headers):
                row_data += [""] * (len(headers) - len(row_data))
            else:
                row_data = row_data[:len(headers)]

            page_data.append(row_data)

        # ⭐ STOP if no NEW companies
        new_rows = []
        for row in page_data:
            name = row[1]
            if name not in existing_names:
                new_rows.append(row)
                existing_names.add(name)

        if not new_rows:
            print(f"✓ No new data at page {page} — stopping early")
            break

        print(f"{screen_slug} → Page {page}: {len(new_rows)} new rows")

        all_rows.extend(new_rows)
        page += 1

    if not all_rows:
        print(f"✗ No data collected for {screen_slug}")
        return None

    df = pd.DataFrame(all_rows, columns=headers)

    print(f"✓ Total companies ({screen_slug}): {len(df)}")
    return df