"""Kitesurf forecast engine — fetch, score, and cache results."""

from __future__ import annotations

import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import requests

from spots_data import Spot, enrich_map_payload, load_all_spots, load_regions, spot_display_fields

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
MAP_CACHE_PATH = BASE_DIR / "map_forecast_cache.json"
API_CACHE_DIR = BASE_DIR / ".cache" / "nl_kite"

TIMEZONE = ZoneInfo("Europe/Amsterdam")
FORECAST_DAYS = 14
MAP_FORECAST_DAYS = 10
MAP_FETCH_WORKERS = 8
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
DAYLIGHT_START = 8
DAYLIGHT_END = 20
DIRECTION_TOLERANCE = 20

WIND_MIN_KTS = 12
WIND_MAX_KTS = 25
WIND_SWEET_MIN = 14
WIND_SWEET_MAX = 22
GUST_MAX_KTS = 30
GUST_MARGINAL_KTS = 28

API_URL = "https://api.open-meteo.com/v1/forecast"
API_CACHE_TTL_SECONDS = 3 * 60 * 60
API_RETRIES = 3
API_RETRY_DELAY = 2

COMPASS_DIRS: dict[str, int] = {
    "N": 0,
    "NE": 45,
    "E": 90,
    "SE": 135,
    "S": 180,
    "SW": 225,
    "W": 270,
    "NW": 315,
}

Level = Literal["beginner", "advanced", "expert"]
Suitability = Literal["Good", "Marginal", "No-go"]
SpotStatus = Literal["good", "marginal", "nogo"]

STATUS_RANK = {"good": 0, "marginal": 1, "nogo": 2}


@dataclass
class DayForecast:
    date: date
    spot_name: str | None
    avg_wind_kts: float | None
    max_gust_kts: float | None
    direction_deg: float | None
    direction_label: str | None
    suitability: Suitability
    level: Level | None
    score: float = 0.0
    in_window: bool = False


@dataclass
class WindowResult:
    start: date
    end: date
    days: list[date]
    gap_days: list[date]
    forecasts: list[DayForecast]
    total_score: float
    window_size: int
    label: str


def angular_distance(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def degrees_to_compass(deg: float) -> str:
    labels = list(COMPASS_DIRS.keys())
    return min(labels, key=lambda lbl: angular_distance(deg, COMPASS_DIRS[lbl]))


def direction_matches(deg: float, good_dirs: tuple[str, ...], tolerance: float = DIRECTION_TOLERANCE) -> bool:
    return any(angular_distance(deg, COMPASS_DIRS[d]) <= tolerance for d in good_dirs)


def vector_mean_direction(directions: list[float], speeds: list[float]) -> float:
    if not directions:
        return 0.0
    u = sum(s * math.sin(math.radians(d)) for d, s in zip(directions, speeds))
    v = sum(s * math.cos(math.radians(d)) for d, s in zip(directions, speeds))
    if u == 0 and v == 0:
        return directions[0] % 360
    return (math.degrees(math.atan2(u, v)) + 360) % 360


def api_cache_path(spot: Spot) -> Path:
    return API_CACHE_DIR / f"{spot.id}.json"


def load_api_cached(spot: Spot) -> dict | None:
    path = api_cache_path(spot)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        if time.time() - payload.get("fetched_at", 0) > API_CACHE_TTL_SECONDS:
            return None
        return payload["data"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def save_api_cache(spot: Spot, data: dict) -> None:
    API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    api_cache_path(spot).write_text(json.dumps({"fetched_at": time.time(), "data": data}))


def fetch_spot_forecast(spot: Spot, *, force_refresh: bool = False) -> dict:
    if not force_refresh:
        cached = load_api_cached(spot)
        if cached is not None:
            return cached

    params = {
        "latitude": spot.lat,
        "longitude": spot.lon,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "wind_speed_unit": "kn",
        "forecast_days": FORECAST_DAYS,
        "timezone": "Europe/Amsterdam",
    }

    last_error: Exception | None = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            response = requests.get(API_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            save_api_cache(spot, data)
            return data
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < API_RETRIES:
                time.sleep(API_RETRY_DELAY * attempt)

    raise RuntimeError(f"Failed to fetch forecast for {spot.name} after {API_RETRIES} attempts: {last_error}")


def parse_hourly(data: dict) -> dict[date, list[dict]]:
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    speeds = hourly.get("wind_speed_10m", [])
    directions = hourly.get("wind_direction_10m", [])
    gusts = hourly.get("wind_gusts_10m", [])

    by_day: dict[date, list[dict]] = {}
    for t, speed, direction, gust in zip(times, speeds, directions, gusts):
        if speed is None or direction is None or gust is None:
            continue
        dt = datetime.fromisoformat(t).replace(tzinfo=TIMEZONE)
        if not (DAYLIGHT_START <= dt.hour < DAYLIGHT_END):
            continue
        day = dt.date()
        by_day.setdefault(day, []).append(
            {"speed": float(speed), "direction": float(direction), "gust": float(gust)}
        )
    return by_day


def assess_spot_day(
    spot: Spot,
    avg_wind: float,
    max_gust: float,
    direction_deg: float,
) -> tuple[Suitability, float]:
    direction_ok = direction_matches(direction_deg, spot.good_directions)
    wind_ok = WIND_MIN_KTS <= avg_wind <= WIND_MAX_KTS
    gust_ok = max_gust <= GUST_MAX_KTS

    if not (direction_ok and wind_ok and gust_ok):
        return "No-go", 0.0

    in_sweet = WIND_SWEET_MIN <= avg_wind <= WIND_SWEET_MAX
    gust_comfortable = max_gust <= GUST_MARGINAL_KTS

    if in_sweet and gust_comfortable:
        score = 3.0 + (avg_wind - WIND_SWEET_MIN) / (WIND_SWEET_MAX - WIND_SWEET_MIN)
        return "Good", score

    score = 1.0
    if in_sweet:
        score += 0.5
    if gust_comfortable:
        score += 0.3
    return "Marginal", score


def _spot_daily_data(spot: Spot, *, force_refresh: bool = False) -> dict[date, dict]:
    data = fetch_spot_forecast(spot, force_refresh=force_refresh)
    hourly_by_day = parse_hourly(data)
    daily: dict[date, dict] = {}

    for day, hours in hourly_by_day.items():
        speeds = [h["speed"] for h in hours]
        directions = [h["direction"] for h in hours]
        gusts = [h["gust"] for h in hours]
        direction_deg = vector_mean_direction(directions, speeds)
        daily[day] = {
            "avg_wind": sum(speeds) / len(speeds),
            "max_gust": max(gusts),
            "direction_deg": direction_deg,
            "direction_label": degrees_to_compass(direction_deg),
        }

    return daily


def build_spot_days(
    spots: tuple[Spot, ...],
    *,
    force_refresh: bool = False,
    max_workers: int = 1,
) -> dict[str, dict[date, dict]]:
    spot_days: dict[str, dict[date, dict]] = {}

    if max_workers <= 1 or len(spots) <= 1:
        for spot in spots:
            logger.info("Fetching %s", spot.name)
            spot_days[spot.name] = _spot_daily_data(spot, force_refresh=force_refresh)
        return spot_days

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_spot_daily_data, spot, force_refresh=force_refresh): spot
            for spot in spots
        }
        for future in as_completed(futures):
            spot = futures[future]
            try:
                spot_days[spot.name] = future.result()
                logger.info("Fetched %s", spot.name)
            except Exception as exc:
                logger.error("Failed to fetch %s: %s", spot.name, exc)

    return spot_days


def day_is_kiteable(forecast: DayForecast) -> bool:
    return forecast.suitability != "No-go"


def find_windows(
    daily_forecasts: list[DayForecast],
    dates: list[date],
    window_size: int,
    max_gaps: int,
) -> list[WindowResult]:
    results: list[WindowResult] = []

    for start_idx in range(len(dates) - window_size + 1):
        window_dates = dates[start_idx : start_idx + window_size]
        window_fc = daily_forecasts[start_idx : start_idx + window_size]

        kiteable = [day_is_kiteable(fc) for fc in window_fc]
        gap_count = kiteable.count(False)
        if gap_count > max_gaps:
            continue

        valid_fc = [fc for fc in window_fc if day_is_kiteable(fc)]
        if not valid_fc:
            continue

        gap_days = [d for d, ok in zip(window_dates, kiteable) if not ok]
        total_score = sum(fc.score for fc in valid_fc) - gap_count * 0.5

        results.append(
            WindowResult(
                start=window_dates[0],
                end=window_dates[-1],
                days=window_dates,
                gap_days=gap_days,
                forecasts=valid_fc,
                total_score=total_score,
                window_size=window_size,
                label="",
            )
        )

    results.sort(
        key=lambda w: (
            -len([d for d in w.days if d not in w.gap_days]),
            -w.total_score,
            len(w.gap_days),
            w.start,
        )
    )
    return results


def format_date_range(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.strftime('%b %d')}–{end.day}"
    return f"{start.strftime('%b %d')}–{end.strftime('%b %d')}"


def pick_best_window(daily_forecasts: list[DayForecast], dates: list[date]) -> tuple[WindowResult | None, str]:
    windows_4_strict = find_windows(daily_forecasts, dates, window_size=4, max_gaps=0)
    windows_4_gap = find_windows(daily_forecasts, dates, window_size=4, max_gaps=1)
    windows_3 = find_windows(daily_forecasts, dates, window_size=3, max_gaps=0)
    windows_2 = find_windows(daily_forecasts, dates, window_size=2, max_gaps=0)

    if windows_4_strict:
        window = windows_4_strict[0]
        window.label = "Recommended 4-day trip (consecutive)"
        return window, window.label
    if windows_4_gap:
        window = windows_4_gap[0]
        window.label = "Recommended 4-day trip (1 rest day allowed)"
        return window, window.label
    if windows_3:
        window = windows_3[0]
        window.label = "Best 3-day trip"
        return window, window.label
    if windows_2:
        window = windows_2[0]
        window.label = "Best 2-day trip"
        return window, window.label
    return None, "No suitable trip window found"


def summarize_window(window: WindowResult) -> str:
    spots = sorted({fc.spot_name for fc in window.forecasts if fc.spot_name})
    winds = [fc.avg_wind_kts for fc in window.forecasts if fc.avg_wind_kts is not None]
    dirs = sorted({fc.direction_label for fc in window.forecasts if fc.direction_label})
    lo, hi = min(winds), max(winds)
    wind_range = f"{lo:.0f}kts" if abs(lo - hi) < 0.5 else f"{lo:.0f}-{hi:.0f}kts"
    spot_text = spots[0] if len(spots) == 1 else f"{spots[0]} (+{len(spots) - 1} alt)"
    dir_text = "/".join(dirs)
    gap_note = f" (1 rest day: {window.gap_days[0].strftime('%b %d')})" if window.gap_days else ""
    return f"Best window: {format_date_range(window.start, window.end)}, {spot_text}, avg {wind_range} {dir_text}{gap_note}"


def _serialize_day(fc: DayForecast) -> dict[str, Any]:
    direction_display = None
    if fc.direction_label is not None and fc.direction_deg is not None:
        direction_display = f"{fc.direction_label} ({fc.direction_deg:.0f}°)"

    return {
        "date": fc.date.isoformat(),
        "date_display": fc.date.strftime("%a %b %d"),
        "spot_name": fc.spot_name,
        "level": fc.level,
        "avg_wind_kts": round(fc.avg_wind_kts, 1) if fc.avg_wind_kts is not None else None,
        "max_gust_kts": round(fc.max_gust_kts, 1) if fc.max_gust_kts is not None else None,
        "direction_deg": round(fc.direction_deg) if fc.direction_deg is not None else None,
        "direction_label": fc.direction_label,
        "direction_display": direction_display,
        "suitability": fc.suitability,
        "in_window": fc.in_window,
    }


def _serialize_window(window: WindowResult | None) -> dict[str, Any] | None:
    if window is None:
        return None

    spots = sorted({fc.spot_name for fc in window.forecasts if fc.spot_name})
    winds = [fc.avg_wind_kts for fc in window.forecasts if fc.avg_wind_kts is not None]
    dirs = sorted({fc.direction_label for fc in window.forecasts if fc.direction_label})
    lo, hi = min(winds), max(winds)
    wind_range = f"{lo:.0f} kn" if abs(lo - hi) < 0.5 else f"{lo:.0f}–{hi:.0f} kn"
    kiteable_dates = [d.isoformat() for d in window.days if d not in window.gap_days]

    return {
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "date_range": format_date_range(window.start, window.end),
        "spot_names": spots,
        "primary_spot": spots[0] if spots else None,
        "wind_range": wind_range,
        "directions": dirs,
        "direction_display": "/".join(dirs),
        "gap_days": [d.isoformat() for d in window.gap_days],
        "kiteable_dates": kiteable_dates,
        "window_size": window.window_size,
        "label": window.label,
        "summary": summarize_window(window),
        "badge": "Good window",
    }


def suitability_to_status(suitability: Suitability) -> SpotStatus:
    if suitability == "Good":
        return "good"
    if suitability == "Marginal":
        return "marginal"
    return "nogo"


def forecast_days_for_spot(
    spot: Spot,
    spot_day_data: dict[date, dict],
    dates: list[date],
) -> list[DayForecast]:
    forecasts: list[DayForecast] = []
    for day in dates:
        day_data = spot_day_data.get(day)
        if not day_data:
            forecasts.append(
                DayForecast(
                    date=day,
                    spot_name=spot.name,
                    avg_wind_kts=None,
                    max_gust_kts=None,
                    direction_deg=None,
                    direction_label=None,
                    suitability="No-go",
                    level=spot.level,
                )
            )
            continue

        suitability, score = assess_spot_day(
            spot,
            day_data["avg_wind"],
            day_data["max_gust"],
            day_data["direction_deg"],
        )
        forecasts.append(
            DayForecast(
                date=day,
                spot_name=spot.name,
                avg_wind_kts=day_data["avg_wind"],
                max_gust_kts=day_data["max_gust"],
                direction_deg=day_data["direction_deg"],
                direction_label=day_data["direction_label"],
                suitability=suitability,
                level=spot.level,
                score=score,
            )
        )
    return forecasts


def summarize_spot_status(forecasts: list[DayForecast]) -> dict[str, Any]:
    good_days = sum(1 for fc in forecasts if fc.suitability == "Good")
    marginal_days = sum(1 for fc in forecasts if fc.suitability == "Marginal")
    nogo_days = sum(1 for fc in forecasts if fc.suitability == "No-go")

    if good_days:
        status: SpotStatus = "good"
        verdict = "Go — viable days in the next 10 days"
    elif marginal_days:
        status = "marginal"
        verdict = "Maybe — only marginal days, check conditions closely"
    else:
        status = "nogo"
        verdict = "No-go — no kiteable days in the forecast window"

    best_window, window_label = pick_best_window(forecasts, [fc.date for fc in forecasts])

    return {
        "status": status,
        "verdict": verdict,
        "good_days": good_days,
        "marginal_days": marginal_days,
        "nogo_days": nogo_days,
        "best_window": _serialize_window(best_window),
        "window_label": window_label,
    }


def summarize_region(spots_payload: list[dict[str, Any]], region_id: str, region_name: str) -> dict[str, Any]:
    region_spots = [spot for spot in spots_payload if spot["region"] == region_id]
    if not region_spots:
        return {
            "id": region_id,
            "name": region_name,
            "spot_count": 0,
            "good_spots": [],
            "marginal_spots": [],
            "recommendation": "No kiteable spots in this region.",
        }

    good_spots = [spot for spot in region_spots if spot["status"] == "good"]
    marginal_spots = [spot for spot in region_spots if spot["status"] == "marginal"]
    ranked = sorted(
        region_spots,
        key=lambda spot: (
            STATUS_RANK[spot["status"]],
            -spot["good_days"],
            -spot["marginal_days"],
            spot["name"],
        ),
    )
    top = ranked[:5]

    if good_spots:
        names = ", ".join(spot["name"] for spot in good_spots[:3])
        extra = len(good_spots) - 3
        recommendation = f"Go — try {names}" + (f" (+{extra} more)" if extra > 0 else "")
    elif marginal_spots:
        names = ", ".join(spot["name"] for spot in marginal_spots[:2])
        recommendation = f"Maybe — marginal only at {names}"
    else:
        recommendation = "No-go — no viable days at any spot in this region"

    return {
        "id": region_id,
        "name": region_name,
        "spot_count": len(region_spots),
        "good_spot_count": len(good_spots),
        "marginal_spot_count": len(marginal_spots),
        "nogo_spot_count": len(region_spots) - len(good_spots) - len(marginal_spots),
        "top_spots": [
            {
                "id": spot["id"],
                "name": spot["name"],
                "status": spot["status"],
                "good_days": spot["good_days"],
                "verdict": spot["verdict"],
            }
            for spot in top
        ],
        "recommendation": recommendation,
    }


def compute_map_forecast(*, force_refresh: bool = False) -> dict[str, Any]:
    today = datetime.now(TIMEZONE).date()
    dates = [today + timedelta(days=i) for i in range(MAP_FORECAST_DAYS)]
    spots = load_all_spots()

    spot_days = build_spot_days(spots, force_refresh=force_refresh, max_workers=MAP_FETCH_WORKERS)
    spots_payload: list[dict[str, Any]] = []

    for spot in spots:
        daily = spot_days.get(spot.name, {})
        forecasts = forecast_days_for_spot(spot, daily, dates)
        summary = summarize_spot_status(forecasts)

        spots_payload.append(
            {
                "id": spot.id,
                "name": spot.name,
                "lat": spot.lat,
                "lon": spot.lon,
                "region": spot.region,
                "policy": spot.policy,
                "level": spot.level,
                "good_directions": list(spot.good_directions),
                "permalink": spot.permalink,
                **spot_display_fields(spot),
                "days": [_serialize_day(fc) for fc in forecasts],
                **summary,
            }
        )

    regions_meta = load_regions()
    region_summaries = [
        summarize_region(spots_payload, region["id"], region["name"]) for region in regions_meta
    ]
    region_summaries.append(
        summarize_region(spots_payload, "other", "Overig")
    )

    return {
        "last_updated": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
        "forecast_days": MAP_FORECAST_DAYS,
        "spots": spots_payload,
        "regions": region_summaries,
    }


def cache_is_fresh(path: Path = MAP_CACHE_PATH, max_age: int = CACHE_MAX_AGE_SECONDS) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text())
        last_updated = datetime.fromisoformat(payload["last_updated"])
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=TIMEZONE)
        age = datetime.now(TIMEZONE) - last_updated
        return age.total_seconds() < max_age
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return False


def empty_map_payload() -> dict[str, Any]:
    today = datetime.now(TIMEZONE).date()
    dates = [today + timedelta(days=i) for i in range(MAP_FORECAST_DAYS)]
    spots = load_all_spots()
    empty_days = [
        _serialize_day(
            DayForecast(
                date=day,
                spot_name=None,
                avg_wind_kts=None,
                max_gust_kts=None,
                direction_deg=None,
                direction_label=None,
                suitability="No-go",
                level=None,
            )
        )
        for day in dates
    ]
    spots_payload = [
        {
            "id": spot.id,
            "name": spot.name,
            "lat": spot.lat,
            "lon": spot.lon,
            "region": spot.region,
            "policy": spot.policy,
            "level": spot.level,
            "good_directions": list(spot.good_directions),
            "permalink": spot.permalink,
            **spot_display_fields(spot),
            "status": "nogo",
            "verdict": "No forecast yet — click Refresh now",
            "good_days": 0,
            "marginal_days": 0,
            "nogo_days": MAP_FORECAST_DAYS,
            "best_window": None,
            "window_label": "No forecast loaded",
            "days": empty_days,
        }
        for spot in spots
    ]
    regions_meta = load_regions()
    region_summaries = [
        summarize_region(spots_payload, region["id"], region["name"]) for region in regions_meta
    ]
    region_summaries.append(summarize_region(spots_payload, "other", "Overig"))
    return {
        "last_updated": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
        "forecast_days": MAP_FORECAST_DAYS,
        "spots": spots_payload,
        "regions": region_summaries,
    }


def load_map_cache(path: Path = MAP_CACHE_PATH) -> dict[str, Any]:
    if not path.exists():
        return empty_map_payload()
    try:
        payload = json.loads(path.read_text())
        return enrich_map_payload(payload)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read map cache: %s", exc)
        return empty_map_payload()


def save_map_cache(payload: dict[str, Any], path: Path = MAP_CACHE_PATH) -> None:
    path.write_text(json.dumps(payload, indent=2))


def run_forecast_update(*, force_refresh: bool = True) -> dict[str, Any]:
    logger.info("Running forecast update for %s spots", len(load_all_spots()))
    payload = compute_map_forecast(force_refresh=force_refresh)
    save_map_cache(payload)
    logger.info("Forecast cache updated at %s", payload["last_updated"])
    return payload
