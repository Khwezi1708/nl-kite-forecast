# NL Kite Forecast

A lightweight local map dashboard for finding kitesurf conditions across the Netherlands. Uses the free [Open-Meteo API](https://open-meteo.com/) and spot metadata from the [NKV Spotkaart](https://kitesurfvereniging.nl/spotkaart/) (allowed + restricted spots only).

## Quick start

```bash
cd ~/Documents/Coding/kite
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open [http://localhost:5001](http://localhost:5001).

> **Note:** Port 5001 is used by default because macOS often reserves 5000 for AirPlay Receiver. Override with `PORT=8080 python app.py` if needed.

## Deploy to Render (free live link)

1. Push this repo to GitHub.
2. Go to [render.com](https://render.com) → **New** → **Web Service** → connect the repo.
3. Render auto-detects `render.yaml`, or set manually:
   - **Build:** `pip install -r requirements.txt`
   - **Start:** `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
   - **Plan:** Free · **Region:** Frankfurt
4. Deploy. Your URL will be `https://<service-name>.onrender.com`.

The repo includes a seed `map_forecast_cache.json` so the map works immediately. Click **Refresh now** to update forecasts.

**Free tier notes:** the service sleeps after ~15 min idle (cold start ~30–60 s). Disk is ephemeral — redeploys clear the API cache in `.cache/`, but the committed forecast seed reloads on boot.

### Production run locally

```bash
gunicorn wsgi:app --bind 0.0.0.0:5001 --workers 1 --timeout 120
```

## What it does

- Interactive **Leaflet map** of **84 kiteable NL spots** (NKV *toegestaan* + *beperkt*)
- Markers colored by next **10-day** forecast: green (go), yellow (maybe), red (no-go)
- **Region filter** with go/maybe/no-go summary and top spot suggestions
- **Spot detail panel** — NKV info (waterdiepte, niveau, windrichtingen, openstelling), daily forecast, best window
- **Auto-refresh** daily at **06:00 Europe/Amsterdam** + manual **Refresh now**

## Cache

- `map_forecast_cache.json` — forecast data for all spots
- Per-spot API cache in `.cache/nl_kite/`

## Rebuild spot database

If NKV spot data changes, update the spotkaart export and run:

```bash
python scripts/build_spots.py
```

## Project layout

```
kite/
├── app.py
├── wsgi.py
├── Procfile
├── render.yaml
├── forecast_engine.py
├── spots_data.py
├── data/spots.json
├── templates/map.html
├── static/map.js
└── map_forecast_cache.json
```

## Scheduler note

The daily refresh only runs while `python app.py` is running. If your laptop is off at 06:00, use **Refresh now** after starting the app.
