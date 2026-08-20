# Polymarket + Kalshi Dashboard (HTML edition)

A local web dashboard — settings page to manage the markets you track,
one click to fetch fresh data, and a report you can copy straight into
an Outlook email. No Excel, no VBA. Runs on Python (reusing the exact
same, already-tested fetching engine from the Excel version — Polymarket/
Kalshi adapters, URL resolution, AUTO time range, price-change math, all
of it, completely unchanged) with a small local Flask web app on top.

**Want your whole organization to see this without anyone but you
running Python?** See **`README-HOSTED.md`** — same app, deployed to a
server so colleagues just visit a URL and always see current data,
refreshed automatically on a timer.

## How this is different from the Excel/VBA attempts

Unlike the VBA rebuild, **this was actually run and tested** — every
route was exercised, the settings save/load flow was verified, and every
piece of visual output (the settings page and the generated report) was
rendered and visually inspected before being handed to you. The one
thing I couldn't test live is the actual Polymarket/Kalshi API calls
themselves (this sandbox can't reach those domains directly), but that
code is 100% unchanged from the Excel version, which was tested
extensively against real responses — nothing about the fetching logic
was touched.

## Setup

1. Extract this ZIP.
2. **Windows:** double-click `RUN DASHBOARD.bat`.
   **Mac/Linux:** run `./run_dashboard.sh` (or `chmod +x run_dashboard.sh` first if needed).
3. Your browser opens automatically to the Settings page. First run
   installs three small dependencies (`flask`, `matplotlib`, `requests`);
   every run after that is instant.

No API keys, no signup, no admin rights. Leave the terminal/command
window open while you use the dashboard — closing it stops the local
server. Nothing is exposed outside your own machine (it only listens on
`127.0.0.1`, not your network).

## Using it

**Settings page** (opens first) — this replaces the Excel CONFIG sheet:
- **+ Add row** for a new market; the **×** button removes one
- **▲ / ▼** reorder rows — the report follows this exact top-to-bottom order
- Same fields as before: Enabled, Platform, URL, Display Type (AUTO
  recommended), Title Override, Time Range (AUTO recommended), Notes
  (supports `ticker=`/`market=`/`market_id=`/`event_id=` overrides)
- **Save** just saves; **Save & Run Report** saves and immediately fetches

**Dashboard page** — the generated report:
- **Copy for email** — selects the report and copies it to your
  clipboard; paste into Outlook (`Ctrl+V`) same as before. This is a
  static image for each chart and a plain styled HTML table for each
  outcome list — deliberately built this way (not a live JavaScript
  chart) specifically so it survives the copy/paste trip into Outlook
  intact, the same reasoning as "Copy as Picture" in the Excel version.
- **Download dashboard.html** — saves the report as a single,
  self-contained file (charts are embedded images, no external files or
  network needed to view it) that you can email as an attachment or drop
  on a shared drive. Anyone can open it in any browser with nothing
  installed — this is the easiest way to **share with other people**.
- **Run again** — re-fetches without leaving the dashboard.

## Sharing this with other people

Two levels, matching what you had with the Excel version:

- **Share a snapshot** ("here's what I'm tracking today") — send the
  downloaded `dashboard.html` file. Opens anywhere, no setup.
- **Share the whole tool** so someone else can maintain their own list
  and run their own reports — send them this whole folder. They need
  Python installed (same one-time step as before), nothing else.

## What's under the hood

```
Polymarket_Kalshi_Dashboard/
├── app.py                      Flask app: routes, settings save, run, dashboard
├── RUN DASHBOARD.bat           Windows launcher
├── run_dashboard.sh            Mac/Linux launcher
├── requirements.txt
├── data/
│   └── config.json             Your market list (replaces the Excel CONFIG sheet)
├── output/
│   └── dashboard.html          Regenerated every run - the shareable static file
├── templates/
│   ├── settings.html           Settings page
│   ├── dashboard.html          Dashboard page (nav + toolbar around the report)
│   └── _macros.html            Reusable row-rendering macro
├── src/
│   ├── market_parser.py        Unchanged from the Excel version
│   ├── polymarket.py           Unchanged
│   ├── kalshi.py                Unchanged
│   ├── utils.py                 Unchanged
│   ├── models.py                Unchanged
│   ├── data_manager.py          Unchanged
│   ├── cache.py                 Unchanged
│   ├── config_json.py           New - JSON config store, replaces the Excel-specific reader
│   ├── chart_image.py           New - renders charts to embedded PNG (matplotlib)
│   └── html_report.py           New - builds the report HTML, replaces excel_report.py
└── tests_engine/                68 automated tests (see below)
```

## Testing

```
pip install pytest responses
python -m pytest tests_engine/ -v
```

68 tests: 60 are the Excel version's original engine tests, run completely
unmodified against these copied-over modules (proving the fetching logic
itself needed zero changes), plus 8 new ones for `config_json.py`. All
passing.

Beyond the automated tests, I actually started the real Flask server (not
just a test client) and hit it with live HTTP requests, and rendered both
the settings page and a populated dashboard to images to visually confirm
the layout, colors, and chart embedding all work — screenshots of both
are what you saw before this file was delivered.

## Limitations / things to know

- **The "Copy for email" button uses your browser's clipboard API**,
  which needs a user gesture (the click itself) to work — if your
  browser blocks it silently, just select the report manually
  (click-drag or `Ctrl+A` inside the white card area) and press `Ctrl+C`.
- **Outlook rendering can still vary** by version — the HTML uses plain
  tables and inline styles specifically to maximize compatibility (the
  same approach used by production HTML email tools), but if something
  looks off after pasting, try **Download dashboard.html** and using
  "Insert → Picture" or a plain paste of the downloaded file's content
  instead.
- This runs a small local web server while in use — that's the "Python
  running in the background" you were trying to get away from with the
  VBA attempt, just simpler and (unlike VBA) actually working. If that's
  still a hard requirement, the **share a snapshot** path above needs no
  server at all once `dashboard.html` is generated.
