#!/usr/bin/env python3
"""
Polymarket + Kalshi Dashboard - hosted edition.

Designed to run continuously on a server (Render, Railway, Fly.io, a
company VM, etc.) rather than just on one person's laptop: a background
thread refreshes the data on a timer, so anyone who visits the URL sees
current data without triggering anything themselves. Also still works
exactly as before for local use - see README-HOSTED.md for both paths.

Routes:
  /            - the dashboard (read-only view, auto-refreshing) - this
                 is the URL you give to colleagues
  /settings    - add/remove/reorder tracked markets (optionally
                 password-protected - see DASHBOARD_USER/DASHBOARD_PASS
                 below)
  /run         - trigger an immediate refresh instead of waiting for the
                 next scheduled one (same protection as /settings)
  /dashboard/download - download the current report as a standalone
                 .html file

Configuration (environment variables, all optional):
  PORT                    - what port to listen on (most hosts set this
                             for you automatically; defaults to 5057
                             for local use)
  REFRESH_INTERVAL_SECONDS - how often to auto-refresh (default 1800 = 30 min)
  DASHBOARD_USER / DASHBOARD_PASS - if BOTH are set, /settings, its save
                             action, and the manual /run trigger require
                             this username/password (HTTP Basic Auth).
                             The main dashboard view stays open to anyone
                             with the link either way - this only gates
                             the ability to change what's tracked or
                             force an extra refresh. Leave unset to leave
                             everything open (fine for a trusted internal
                             network; not recommended for a public URL).
"""
from __future__ import annotations

import functools
import logging
import os
import sys
import threading
import time
import webbrowser

from flask import Flask, Response, redirect, render_template, request, send_file, url_for

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import config_json, data_manager, html_report  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "data", "config.json")
CACHE_PATH = os.path.join(BASE_DIR, "data", "cache.json")
DB_PATH = os.path.join(BASE_DIR, "data", "market_history.db")
LOG_DIR = os.path.join(BASE_DIR, "logs")
OUTPUT_HTML_PATH = os.path.join(BASE_DIR, "output", "dashboard.html")

DEFAULT_PORT = 5057
REFRESH_INTERVAL_SECONDS = int(os.environ.get("REFRESH_INTERVAL_SECONDS", 30 * 60))
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "")
AUTH_ENABLED = bool(DASHBOARD_USER and DASHBOARD_PASS)

# A PORT env var being set is how essentially every PaaS (Render,
# Railway, Heroku-style platforms, ...) tells your app which port to
# bind - its presence is a reasonable, standard signal that we're
# running hosted rather than on someone's laptop, so behavior that only
# makes sense locally (opening a browser window) can key off it without
# needing a separate "am I hosted" flag to remember to set.
IS_HOSTED = "PORT" in os.environ
PORT = int(os.environ.get("PORT", DEFAULT_PORT))

app = Flask(__name__)

# Shared in-memory state, refreshed by the background thread. Also
# written to output/dashboard.html on every refresh so it survives a
# restart and so "download the report" always has something to serve.
#
# IMPORTANT if deploying with a WSGI server that supports multiple
# worker processes (gunicorn, etc.): use exactly ONE worker
# (`gunicorn -w 1 app:app`). Each worker is a separate process with its
# own copy of this dict and its own background thread - more than one
# worker means redundant concurrent fetches and viewers randomly seeing
# different workers' independently-refreshed (and therefore
# out-of-sync) data. A single worker is entirely adequate for this kind
# of low-traffic internal dashboard.
_state = {"results": None, "stats": None, "issues": [], "ran_at": None}
_state_lock = threading.Lock()


def _get_logger() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("dashboard")
    if not logger.handlers:
        handlers = [logging.StreamHandler()]  # always show logs in the host's log viewer
        try:
            handlers.append(logging.FileHandler(os.path.join(LOG_DIR, "dashboard.log")))
        except OSError:
            pass  # some hosts have a read-only filesystem outside a designated volume - fine, stream handler still works
        for h in handlers:
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(h)
        logger.setLevel(logging.INFO)
    return logger


def _refresh_now() -> None:
    """Fetches every enabled market and updates the shared state. Safe to
    call from the background thread or from a request handler."""
    logger = _get_logger()
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(OUTPUT_HTML_PATH), exist_ok=True)

    read_result = config_json.read_config(CONFIG_PATH)
    enabled_rows = [r for r in read_result.rows if r.enabled]
    if not enabled_rows:
        logger.info("Refresh skipped: no enabled rows in CONFIG.")
        return

    start = time.monotonic()
    try:
        results, stats = data_manager.run_all(read_result.rows, CACHE_PATH, DB_PATH, logger)
    except Exception:
        logger.exception("Refresh failed")
        return
    runtime = time.monotonic() - start

    full_html = html_report.build_dashboard_html(results, stats)
    with open(OUTPUT_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(full_html)

    with _state_lock:
        _state["results"] = results
        _state["stats"] = stats
        _state["issues"] = read_result.issues
        _state["ran_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    logger.info("Refresh complete: %d results in %.1fs", len(results), runtime)


def _background_refresh_loop() -> None:
    logger = _get_logger()
    logger.info("Background refresh loop started (every %ds)", REFRESH_INTERVAL_SECONDS)
    while True:
        _refresh_now()
        time.sleep(REFRESH_INTERVAL_SECONDS)


def _start_background_thread_once() -> None:
    # A simple module-level flag, not a lock-protected singleton - this
    # only needs to prevent starting the thread twice within one process
    # (e.g. if Flask's reloader re-imports this module in debug mode),
    # not to coordinate across separate worker processes, each of which
    # legitimately gets its own thread (see the single-worker note above
    # for why you should only run one worker process at all).
    if getattr(_start_background_thread_once, "_started", False):
        return
    _start_background_thread_once._started = True
    threading.Thread(target=_background_refresh_loop, daemon=True).start()


def _require_auth(view):
    """Decorator gating a route behind HTTP Basic Auth, only when both
    DASHBOARD_USER and DASHBOARD_PASS are configured. A no-op otherwise,
    so this project keeps working exactly as before for anyone who
    doesn't set those two variables."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not AUTH_ENABLED:
            return view(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.username != DASHBOARD_USER or auth.password != DASHBOARD_PASS:
            return Response(
                "Authentication required.", 401,
                {"WWW-Authenticate": 'Basic realm="Dashboard settings"'},
            )
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
def dashboard():
    with _state_lock:
        results, stats, issues, ran_at = (
            _state["results"], _state["stats"], _state["issues"], _state["ran_at"],
        )

    if results is None:
        if os.path.exists(OUTPUT_HTML_PATH):
            return send_file(OUTPUT_HTML_PATH)
        return render_template(
            "dashboard.html", report_fragment="", ran_at=None, stats=None, issues=[],
            not_ready=True, refresh_interval=REFRESH_INTERVAL_SECONDS,
        )

    report_fragment = html_report.build_dashboard_html(results, stats, embed_page_chrome=False)
    return render_template(
        "dashboard.html",
        report_fragment=report_fragment,
        ran_at=ran_at,
        stats=stats,
        issues=issues,
        not_ready=False,
        refresh_interval=REFRESH_INTERVAL_SECONDS,
    )


@app.route("/dashboard")
def dashboard_alias():
    # Kept as an alias for anyone with the old link/bookmark from the
    # single-user local version - / is the canonical URL now.
    return redirect(url_for("dashboard"))


@app.route("/settings", methods=["GET"])
@_require_auth
def settings():
    config_json.ensure_config_exists(CONFIG_PATH)
    rows = config_json.load_raw_rows(CONFIG_PATH)
    message = request.args.get("message", "")
    return render_template("settings.html", rows=rows, message=message, auth_enabled=AUTH_ENABLED)


@app.route("/settings/save", methods=["POST"])
@_require_auth
def settings_save():
    row_tokens = request.form.getlist("row_token")
    urls = request.form.getlist("url")
    platforms = request.form.getlist("platform")
    display_types = request.form.getlist("display_type")
    title_overrides = request.form.getlist("title_override")
    time_ranges = request.form.getlist("time_range")
    notes_list = request.form.getlist("notes")
    checked_tokens = set(request.form.getlist("enabled_tokens"))

    rows = []
    for i, token in enumerate(row_tokens):
        url = urls[i].strip() if i < len(urls) else ""
        if not url:
            continue  # a fully-blank added-then-untouched row - skip silently
        rows.append({
            "enabled": token in checked_tokens,
            "platform": platforms[i].strip() if i < len(platforms) else "",
            "url": url,
            "display_type": display_types[i].strip() if i < len(display_types) else "AUTO",
            "title_override": title_overrides[i].strip() if i < len(title_overrides) else "",
            "time_range": time_ranges[i].strip() if i < len(time_ranges) else "AUTO",
            "notes": notes_list[i].strip() if i < len(notes_list) else "",
        })

    config_json.save_raw_rows(CONFIG_PATH, rows)

    if request.form.get("action") == "save_and_run":
        return redirect(url_for("run_report"))
    return redirect(url_for("settings", message="Saved."))


@app.route("/run", methods=["GET", "POST"])
@_require_auth
def run_report():
    read_result = config_json.read_config(CONFIG_PATH)
    enabled_rows = [r for r in read_result.rows if r.enabled]
    if not enabled_rows:
        return redirect(url_for(
            "settings",
            message="No enabled rows to fetch - check at least one row's Enabled box first.",
        ))
    _refresh_now()
    return redirect(url_for("dashboard"))


@app.route("/dashboard/download")
def dashboard_download():
    if not os.path.exists(OUTPUT_HTML_PATH):
        return redirect(url_for("settings", message="No report yet - it refreshes automatically; check back shortly."))
    return send_file(OUTPUT_HTML_PATH, as_attachment=True, download_name="dashboard.html")


def _open_browser_when_ready() -> None:
    time.sleep(1.0)
    webbrowser.open(f"http://127.0.0.1:{PORT}/")


config_json.ensure_config_exists(CONFIG_PATH)
_start_background_thread_once()

if __name__ == "__main__":
    if not IS_HOSTED:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    print(f"Starting dashboard on port {PORT}  (Ctrl+C to stop)")
    if AUTH_ENABLED:
        print("Settings/run are password-protected (DASHBOARD_USER/DASHBOARD_PASS set).")
    else:
        print("Settings/run are OPEN to anyone with the URL (set DASHBOARD_USER and "
              "DASHBOARD_PASS to protect them).")
    app.run(host="0.0.0.0", port=PORT, debug=False)
