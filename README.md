# DIBBS RFQ Browser

Scrapes DIBBS RFQs for a post date into SQLite and serves a filterable browser.

```
py -m pip install requests beautifulsoup4 flask
py scraper.py 11-20-2026      # scrape Friday's RFQs -> dibbs.db
py app.py                     # http://localhost:5000
```

- **scraper.py** — accepts the DoD consent banner, walks every ASP.NET grid page (`Page$N` postbacks), parses all 9 columns plus sub-fields (item tags, tech-doc links, set-aside, fast-award, quote link, QTY), upserts into `rfqs`.
- **dibbs.db** — SQLite; PK `(solicitation, nsn, pr_number)`, so re-scraping is idempotent and multiple dates accumulate.
- **app.py** — Flask: `/api/dates`, `/api/rfqs?date=YYYY-MM-DD`, serves `static/index.html`.
- **UI** — same columns/layout as DIBBS, filter under every column (text, dropdowns, qty range, date ranges), sortable headers, 50/page, links back to DIBBS package view / RFQ PDF / NSN view / quote form.
