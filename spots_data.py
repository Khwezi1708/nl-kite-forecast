"""Load kitesurf spot metadata from data/spots.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

Level = Literal["beginner", "advanced", "expert"]
Policy = Literal["allowed", "restricted"]

BASE_DIR = Path(__file__).resolve().parent
SPOTS_PATH = BASE_DIR / "data" / "spots.json"
REGIONS_PATH = BASE_DIR / "data" / "regions.json"


@dataclass(frozen=True)
class Spot:
    id: str
    name: str
    lat: float
    lon: float
    region: str
    policy: Policy
    good_directions: tuple[str, ...]
    windrichtingen: tuple[str, ...]
    waterdiepte: tuple[str, ...]
    niveau: tuple[str, ...]
    level: Level
    openstelling: str
    permalink: str


def spot_display_fields(spot: Spot) -> dict[str, list[str] | str]:
    return {
        "waterdiepte": list(spot.waterdiepte),
        "niveau": list(spot.niveau),
        "windrichtingen": list(spot.windrichtingen),
        "openstelling": spot.openstelling,
    }


def enrich_map_payload(payload: dict) -> dict:
    """Merge latest spot metadata into cached forecast payload."""
    by_id = {spot.id: spot for spot in load_all_spots()}
    for entry in payload.get("spots", []):
        meta = by_id.get(entry.get("id", ""))
        if meta is not None:
            entry.update(spot_display_fields(meta))
    return payload


@lru_cache(maxsize=1)
def load_regions() -> list[dict]:
    payload = json.loads(REGIONS_PATH.read_text())
    return payload["regions"]


@lru_cache(maxsize=1)
def load_all_spots() -> tuple[Spot, ...]:
    raw = json.loads(SPOTS_PATH.read_text())
    spots: list[Spot] = []
    for entry in raw:
        spots.append(
            Spot(
                id=entry["id"],
                name=entry["name"],
                lat=float(entry["lat"]),
                lon=float(entry["lon"]),
                region=entry["region"],
                policy=entry["policy"],
                good_directions=tuple(entry["good_directions"]),
                windrichtingen=tuple(entry.get("windrichtingen") or entry["good_directions"]),
                waterdiepte=tuple(entry.get("waterdiepte") or []),
                niveau=tuple(entry.get("niveau") or ["Gevorderd"]),
                level=entry["level"],
                openstelling=entry.get("openstelling", ""),
                permalink=entry.get("permalink", ""),
            )
        )
    return tuple(spots)


def get_spot(spot_id: str) -> Spot | None:
    return next((spot for spot in load_all_spots() if spot.id == spot_id), None)
