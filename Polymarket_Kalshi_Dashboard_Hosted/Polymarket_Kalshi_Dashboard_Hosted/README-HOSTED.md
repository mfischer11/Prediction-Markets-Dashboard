# Polymarket + Kalshi Dashboard — hosted edition

Runs continuously on a server instead of your laptop: a background job
refreshes the data on a timer, and anyone in your org can visit a URL to
see current results — nobody except you (the person maintaining the
market list) ever needs to touch Python, or anything else.

## How this differs from the local version

- **`/`** is now the dashboard itself (not settings) — this is the URL
  you share with colleagues. It auto-refreshes in the browser every
  time the background job runs, so someone can leave the tab open and
  just watch it update.
- **A background thread refreshes automatically** on a timer (default
  every 30 minutes) — nobody needs to click "Run Report" for others to
  see fresh data.
- **`/settings`** (add/remove/reorder markets) can be optionally
  password-protected, since anyone with the link could otherwise change
  what's being tracked. The dashboard view itself always stays open.

## Deploying (using Render.com as the example — free tier available)

Any host that runs a Python web app works the same way; Render is just
one of the simpler ones to get started with. Railway, Fly.io, and a
plain VM all work too — skip to "Deploying anywhere else" below if
you'd rather use one of those.

1. **Put this folder in a Git repository** (GitHub, GitLab, etc.) — for
   example, create a new repo on GitHub, then from this folder:
   ```
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin <your-repo-url>
   git push -u origin main
   ```
2. Go to [render.com](https://render.com), sign up (free), click
   **New → Web Service**, and connect the repository you just pushed.
3. Render should auto-detect the **`Procfile`** included here and use it
   to start the app — if asked, confirm:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn -w 1 --timeout 120 app:app` (already
     the default via the Procfile)
4. **Environment variables** (Render's "Environment" tab) — all
   optional, but worth setting:
   - `DASHBOARD_USER` and `DASHBOARD_PASS` — protects the Settings page.
     Skip these only if you're fine with anyone who has the URL being
     able to change what's tracked.
   - `REFRESH_INTERVAL_SECONDS` — how often to refresh (default 1800 =
     30 minutes). Don't set this too low; each refresh makes one API
     call per tracked market, and very frequent polling isn't
     necessary for prediction markets that don't move that fast.
5. Click **Create Web Service**. Render builds and starts it — takes a
   couple of minutes the first time. You'll get a URL like
   `https://your-app-name.onrender.com` — **that's the link you share
   with your organization.**
6. Visit `https://your-app-name.onrender.com/settings` yourself (log in
   if you set a password) and add your tracked markets, then **Save &
   Run Report** to get the first refresh going immediately instead of
   waiting for the timer.

**A note on Render's free tier specifically:** free web services on
Render go to sleep after a period of no incoming traffic, and waking
back up takes a few seconds on the next visit. For a small internal
tool this is usually a non-issue (someone visiting wakes it up), but if
you want it to *never* sleep and always be instantly ready, look at
Render's paid "Starter" tier, or an alternative host without a sleep
policy (Railway and Fly.io both have this same free-tier trade-off;
PythonAnywhere's free tier does not sleep but has other limits).

## Deploying anywhere else

Any host that can run `pip install -r requirements.txt` followed by
`gunicorn -w 1 --timeout 120 app:app` (with a `PORT` environment
variable telling it what to bind to — nearly every PaaS sets this
automatically) will work the same way. The **`-w 1`** (exactly one
worker process) matters — see the comment at the top of `app.py` for
why: more than one worker means redundant concurrent fetches and
viewers randomly seeing different workers' out-of-sync data.

For a plain Linux VM instead of a PaaS: install Python 3.10+, run the
same two commands inside the project folder (ideally under `systemd` or
`tmux`/`screen` so it keeps running after you disconnect), and point
your organization at the server's address (optionally behind a reverse
proxy like nginx if you want a real domain name or HTTPS).

## Running it locally first (recommended before deploying)

Exactly like the local version:
```
pip install -r requirements.txt
python app.py
```
Opens `http://127.0.0.1:5057/` automatically. This is a good way to add
your market list and confirm everything looks right before you push it
to a public URL.

## Security notes

- The dashboard view (`/`) is intentionally always open to anyone with
  the link — it's meant to be shared. Only `/settings`, its save
  action, and the manual `/run` trigger are gated by
  `DASHBOARD_USER`/`DASHBOARD_PASS`, and only if you set both.
- This is HTTP Basic Auth — simple, and fine for an internal tool over
  HTTPS (which Render and similar hosts provide automatically), but not
  meant for anything more sensitive than "keep casual visitors from
  editing the list."
- Nothing here handles user accounts, rate limiting, or DDoS protection
  — appropriate for a small internal team's dashboard, not for putting
  in front of the general public.

## Everything else

Settings fields, Notes overrides, the price-change math, chart
rendering, "Copy for email," and "Download dashboard.html" all work
exactly as in the local version — see the main `README.md` for those.
