"""Scrape DIBBS RFQs posted on a given date into SQLite.

Usage: py scraper.py [MM-DD-YYYY]   (default 11-20-2026)
"""
import re
import sqlite3
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE = "https://www.dibbs.bsm.dla.mil"
GRID = "ctl00$cph1$grdRfqSearch"
DB_PATH = "dibbs.db"
HEADERS = {
    # DIBBS sits behind an F5 WAF that rejects non-browser-looking requests.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS rfqs (
    solicitation    TEXT NOT NULL,
    nsn             TEXT NOT NULL,
    post_date       TEXT NOT NULL,          -- ISO yyyy-mm-dd
    row_num         INTEGER,
    fsc             TEXT,                   -- first 4 digits of NSN (Federal Supply Class)
    nsn_url         TEXT,
    item_tags       TEXT,                   -- e.g. "Mil-Spec"
    nomenclature    TEXT,
    tech_docs       TEXT,                   -- "Tech Docs" / "None"
    tech_docs_url   TEXT,
    tech_docs_note  TEXT,                   -- e.g. "*Spec/Stnd Only"
    sol_pdf_url     TEXT,
    package_url     TEXT,
    set_aside       TEXT,
    fast_award      INTEGER,
    status          TEXT,
    quote_url       TEXT,
    pr_number       TEXT,
    qty             INTEGER,
    issued          TEXT,                   -- ISO
    return_by       TEXT,                   -- ISO
    scraped_at      TEXT,
    PRIMARY KEY (solicitation, nsn, pr_number)
);
CREATE INDEX IF NOT EXISTS idx_rfqs_post_date ON rfqs(post_date);
"""


def iso(d):
    d = (d or "").strip()
    try:
        return datetime.strptime(d, "%m-%d-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return d or None


def hidden_fields(soup):
    form = soup.find("form")
    return {i["name"]: i.get("value", "")
            for i in form.select("input[type=hidden]") if i.get("name")}


def open_session(url):
    s = requests.Session()
    s.headers.update(HEADERS)
    r = s.get(url, timeout=60)
    if "dodwarning" in r.url.lower():
        soup = BeautifulSoup(r.text, "html.parser")
        data = hidden_fields(soup)
        data["butAgree"] = "OK"
        s.post(requests.compat.urljoin(r.url, soup.find("form")["action"]), data=data, timeout=60)
        r = s.get(url, timeout=60)
    if "Request Rejected" in r.text:
        raise RuntimeError("Blocked by DIBBS WAF")
    return s, r


def cell_link(td, pred=None):
    for a in td.find_all("a", href=True):
        if pred is None or pred(a):
            return a
    return None


def parse_row(tr):
    tds = tr.find_all("td", recursive=False)
    num, nsn_td, nom_td, tech_td, sol_td, status_td, pr_td, iss_td, ret_td = tds

    nsn_a = cell_link(nsn_td)
    nsn = (nsn_a.get_text(strip=True) if nsn_a else nsn_td.get_text(" ", strip=True)).strip()
    tags = [sp.get_text(strip=True) for sp in nsn_td.select("span span")]

    tech_a = cell_link(tech_td)
    tech_note = tech_td.select_one(".cFoldersSpecs")

    sol_a = cell_link(sol_td, lambda a: a.get("title") == "RFQ document")
    pkg_a = cell_link(sol_td, lambda a: "Package View" in a.get_text())
    alts = [img.get("alt", "").strip() for img in sol_td.find_all("img")]
    fast = any(a.startswith("Fast Award") for a in alts)
    set_aside = next((a for a in alts if a and not a.startswith("Fast Award")), None)

    status_span = status_td.select_one("span span")
    quote_a = cell_link(status_td, lambda a: "QuoteFrm" in a["href"])

    pr_text = pr_td.get_text(" ", strip=True)
    pr_m = re.match(r"(\S+)", pr_text)
    qty_m = re.search(r"QTY:\s*([\d,]+)", pr_text)

    digits = re.sub(r"\D", "", nsn)
    return {
        "row_num": int(num.get_text(strip=True) or 0),
        "nsn": nsn,
        "fsc": digits[:4] if len(digits) >= 13 else None,
        "nsn_url": nsn_a["href"] if nsn_a else None,
        "item_tags": ", ".join(tags) or None,
        "nomenclature": nom_td.get_text(" ", strip=True),
        "tech_docs": "Tech Docs" if tech_a else tech_td.get_text(" ", strip=True).split("*")[0].strip() or "None",
        "tech_docs_url": tech_a["href"] if tech_a else None,
        "tech_docs_note": tech_note.get_text(strip=True) if tech_note else None,
        "solicitation": sol_a.get_text(strip=True) if sol_a else sol_td.get_text(" ", strip=True).split()[0],
        "sol_pdf_url": sol_a["href"] if sol_a else None,
        "package_url": pkg_a["href"] if pkg_a else None,
        "set_aside": set_aside,
        "fast_award": int(fast),
        "status": status_span.get_text(strip=True) if status_span else status_td.get_text(" ", strip=True).split()[0],
        "quote_url": quote_a["href"] if quote_a else None,
        "pr_number": pr_m.group(1) if pr_m else "",
        "qty": int(qty_m.group(1).replace(",", "")) if qty_m else None,
        "issued": iso(iss_td.get_text(strip=True)),
        "return_by": iso(ret_td.get_text(strip=True)),
    }


def parse_page(html):
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find("table", id="ctl00_cph1_grdRfqSearch")
    rows = [] if grid is None else [
        parse_row(tr) for tr in grid.find_all("tr", recursive=False)
        if tr.get("class") and tr["class"][0] in ("BgWhite", "BgSilver")
    ]
    m = re.search(r"Records Found:\s*([\d,]+)", soup.get_text(" ", strip=True))
    total = int(m.group(1).replace(",", "")) if m else None
    return soup, rows, total


def scrape(date_str):
    url = f"{BASE}/RFQ/RfqRecs.aspx?category=post&TypeSrch=dt&Value={date_str}"
    s, r = open_session(url)
    soup, rows, total = parse_page(r.text)
    all_rows = list(rows)
    page = 1
    print(f"Records found: {total}; page 1 -> {len(rows)} rows")
    while total and len(all_rows) < total:
        page += 1
        data = hidden_fields(soup)
        data["__EVENTTARGET"] = GRID
        data["__EVENTARGUMENT"] = f"Page${page}"
        r = s.post(url, data=data, timeout=60)
        soup, rows, _ = parse_page(r.text)
        if not rows:
            print(f"page {page}: no rows, stopping")
            break
        all_rows.extend(rows)
        print(f"page {page} -> {len(rows)} rows ({len(all_rows)}/{total})")
        time.sleep(0.5)  # be polite
    return all_rows


def save(rows, date_str):
    post_date = iso(date_str)
    now = datetime.now().isoformat(timespec="seconds")
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    cols = list(rows[0].keys()) + ["post_date", "scraped_at"] if rows else []
    con.executemany(
        f"INSERT OR REPLACE INTO rfqs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [tuple(r.values()) + (post_date, now) for r in rows],
    )
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM rfqs WHERE post_date=?", (post_date,)).fetchone()[0]
    con.close()
    print(f"Saved. {n} rows in DB for {post_date}")


if __name__ == "__main__":
    date_str = sys.argv[1] if len(sys.argv) > 1 else "11-20-2026"
    save(scrape(date_str), date_str)
