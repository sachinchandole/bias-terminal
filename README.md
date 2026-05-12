# Market Bias Terminal — NSE Auto-Fetch Dashboard

Self-hosted dashboard that pulls Nifty / Bank Nifty option chain, India VIX,
FII / DII cash flow, and participant-wise OI from NSE — then computes a
weighted daily bias and trade plan.

You'll deploy this once and get a free public URL like
`https://your-name-bias.onrender.com` that you bookmark on your phone.

---

## How it works

```
┌────────────────┐    fetch     ┌──────────────────┐   nselib   ┌────────┐
│  your browser  │ ───────────► │  Flask backend   │ ────────►  │  NSE   │
│  (dashboard)   │ ◄──────────  │  (Render free)   │ ◄────────  │ public │
└────────────────┘   JSON       └──────────────────┘   scrape    └────────┘
```

The backend uses `nselib` to scrape NSE's public reports and returns clean
JSON to the dashboard. CORS is no longer a problem because the browser only
ever talks to your own backend.

---

## Step 1 — Get the code into a GitHub repo

You need a GitHub account (free). Then either:

**Option A — upload via web UI (no command line needed):**
1. Sign in to github.com → click **+ → New repository**
2. Name it `bias-terminal`, make it Public or Private, click *Create*
3. Click **uploading an existing file** on the empty repo page
4. Drag-and-drop **every file in this folder** (including `static/`) onto the page
5. Click *Commit changes*

**Option B — via git CLI (faster if you have git installed):**
```bash
cd bias_terminal
git init
git add .
git commit -m "initial"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/bias-terminal.git
git push -u origin main
```

---

## Step 2 — Deploy on Render

1. Go to [render.com](https://render.com) and sign up (use the *Sign in with GitHub* button — easier)
2. From the dashboard click **New + → Web Service**
3. Connect your GitHub account and select the `bias-terminal` repo
4. Render will detect `render.yaml` and auto-fill most settings. Confirm:
   - **Region:** Singapore (closest to NSE servers from a Render free node)
   - **Plan:** Free
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT`
5. Click **Create Web Service**

First deploy takes 3–5 minutes (installing pandas + nselib). When done, the
URL appears at the top — looks like `https://bias-terminal-xxxx.onrender.com`.

Open it on your phone and bookmark it.

---

## Step 3 — Use it

1. Tap **NIFTY 50** or **BANK NIFTY** tab
2. Tap **↓ Fetch Now** — backend pulls everything from NSE (~5–8 seconds)
3. Fields auto-fill, marked green where data was fetched live
4. Anything missing (NSE endpoints occasionally fail), enter manually
5. Tap **⚙ Compute Bias** to run the scoring engine
6. Tap **📋 Copy Summary** to copy a clean message → paste into WhatsApp
7. Tap **💾 Save Today's Read** to track in the history table

---

## Free-tier gotcha (and the fix)

Render free instances **spin down after 15 minutes of no traffic**. The first
request after a cold spin takes ~50 seconds. Two ways to handle:

**Option A — accept the cold start.** First load slow, subsequent fast.

**Option B — keep it warm with UptimeRobot (free):**
1. Sign up at [uptimerobot.com](https://uptimerobot.com)
2. Add Monitor → HTTP(s) → URL = `https://your-url.onrender.com/api/health`
3. Interval: 5 minutes
4. Render now stays warm during market hours (and dies overnight, saving free-tier hours)

**Option C — upgrade.** Render Starter is $7/month, always-on.

---

## When NSE breaks (it will, occasionally)

NSE changes endpoints every few months and breaks unofficial scrapers like
`nselib`. When that happens:

1. `/api/health` will still return ok, but `/api/snapshot/NIFTY` returns errors
2. Upgrade nselib: edit `requirements.txt`, bump the version, push to GitHub, Render auto-redeploys
3. If still broken, switch to manual entry — the dashboard works fine either way
4. Issues filed against `nselib` on GitHub usually get patched within a week

---

## Files in this project

| File | Purpose |
|------|---------|
| `app.py` | Flask web app — serves HTML + JSON API |
| `nse_fetcher.py` | All NSE data fetching + bias-relevant computations |
| `static/index.html` | Single-file dashboard (HTML/CSS/JS) |
| `requirements.txt` | Python dependencies |
| `Procfile` | gunicorn launch command |
| `render.yaml` | Render auto-deploy config |
| `runtime.txt` | Pins Python 3.11 |

---

## Adding a password (recommended if you deploy publicly)

The dashboard is currently open to anyone with the URL. To add basic auth,
edit `app.py` and wrap routes with a check against an env var:

```python
from functools import wraps
from flask import request, Response
import os

def require_auth(f):
    @wraps(f)
    def wrapped(*a, **kw):
        auth = request.authorization
        if not auth or auth.password != os.environ.get('PASSWORD'):
            return Response('Auth required', 401, {'WWW-Authenticate': 'Basic'})
        return f(*a, **kw)
    return wrapped

# then decorate routes:
@app.route('/')
@require_auth
def root(): ...
```

Then in Render → Environment, add `PASSWORD=yoursecret`. Browser will prompt
for username (anything) + password on first load.

---

## Costs

| Item | Cost |
|------|------|
| Render free web service | ₹0 |
| GitHub free | ₹0 |
| UptimeRobot free | ₹0 |
| **Total** | **₹0/month** |

Upgrades only if you want guaranteed always-on (~₹600/month for Render Starter).

---

## Not investment advice

This is an educational/analysis tool. Trade with your own risk management.
