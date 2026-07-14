"""NL Kite Forecast — local map dashboard."""

from __future__ import annotations

import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, url_for

from forecast_engine import (
    MAP_CACHE_PATH,
    TIMEZONE,
    cache_is_fresh,
    load_map_cache,
    run_forecast_update,
)
from spots_data import get_spot, load_regions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_PORT = 5001
_services_started = False

app = Flask(__name__)
scheduler = BackgroundScheduler(timezone=TIMEZONE)


def ensure_forecast_on_startup() -> None:
    if MAP_CACHE_PATH.exists():
        logger.info("Using existing forecast cache on startup")
        return
    logger.info("No cache found — run Refresh to fetch forecasts")


def format_last_updated(iso_timestamp: str) -> str:
    dt = datetime.fromisoformat(iso_timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TIMEZONE)
    return dt.strftime("%a %d %b %Y, %H:%M %Z")


@app.route("/")
def map_view():
    map_data = load_map_cache()
    regions = load_regions()
    map_data["last_updated_display"] = format_last_updated(map_data["last_updated"])
    map_data["cache_is_fresh"] = cache_is_fresh(MAP_CACHE_PATH)
    return render_template(
        "map.html",
        map_data=map_data,
        regions=regions,
    )


@app.get("/api/map")
def api_map():
    region = request.args.get("region")
    payload = load_map_cache()
    spots = payload["spots"]
    if region and region != "all":
        spots = [spot for spot in spots if spot["region"] == region]
    regions = payload["regions"]
    if region and region != "all":
        regions = [item for item in regions if item["id"] == region]
    return jsonify(
        {
            "last_updated": payload["last_updated"],
            "forecast_days": payload["forecast_days"],
            "spots": spots,
            "regions": regions,
        }
    )


@app.get("/api/spots/<spot_id>")
def api_spot(spot_id: str):
    payload = load_map_cache()
    spot = next((item for item in payload["spots"] if item["id"] == spot_id), None)
    if spot is None:
        meta = get_spot(spot_id)
        if meta is None:
            return jsonify({"error": "Spot not found"}), 404
        return jsonify({"error": "Forecast not loaded for this spot yet"}), 404
    return jsonify(spot)


@app.post("/refresh")
def refresh():
    run_forecast_update()
    return redirect(url_for("map_view"))


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(
        app.static_folder,
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@app.get("/sw.js")
def service_worker():
    response = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


def bootstrap_services() -> None:
    global _services_started
    if _services_started:
        return
    _services_started = True
    ensure_forecast_on_startup()
    start_scheduler()


def start_scheduler() -> None:
    scheduler.add_job(
        run_forecast_update,
        CronTrigger(hour=6, minute=0, timezone=TIMEZONE),
        id="daily_forecast_refresh",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduled daily forecast refresh at 06:00 Europe/Amsterdam")


if __name__ == "__main__":
    bootstrap_services()
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    logger.info("Open http://127.0.0.1:%s in your browser", port)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
