#!/usr/bin/env python3
"""Build data/spots.json and data/regions.json from NKV spotkaart export."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(
    "/Users/khwezik/.cursor/projects/Users-khwezik-Documents-Coding/uploads/spotkaart-0.md"
)
OUT_SPOTS = ROOT / "data" / "spots.json"
OUT_REGIONS = ROOT / "data" / "regions.json"

WIND_MAP = {
    "Noord": "N",
    "Noordwest": "NW",
    "West": "W",
    "Zuidwest": "SW",
    "Zuid": "S",
    "Zuidoost": "SE",
    "Oost": "E",
    "Noordoost": "NE",
}
LEVEL_MAP = {"Beginner": "beginner", "Gevorderd": "advanced", "Expert": "expert"}
POLICY_MAP = {"toegestaan": "allowed", "beperkt": "restricted", "verboden": "forbidden"}
LEVEL_RANK = {"beginner": 0, "advanced": 1, "expert": 2}

REGIONS = [
    {
        "id": "zeeland",
        "name": "Zeeland",
        "bounds": {"south": 51.25, "north": 51.72, "west": 3.30, "east": 4.35},
    },
    {
        "id": "zuid-holland",
        "name": "Zuid-Holland",
        "bounds": {"south": 51.85, "north": 52.25, "west": 3.95, "east": 4.65},
    },
    {
        "id": "noord-holland",
        "name": "Noord-Holland",
        "bounds": {"south": 52.25, "north": 53.10, "west": 4.50, "east": 5.15},
    },
    {
        "id": "friesland",
        "name": "Friesland & Waddeneilanden",
        "bounds": {"south": 52.85, "north": 53.55, "west": 4.70, "east": 6.30},
    },
    {
        "id": "groningen",
        "name": "Groningen",
        "bounds": {"south": 53.05, "north": 53.55, "west": 6.05, "east": 7.05},
    },
    {
        "id": "flevoland",
        "name": "Flevoland",
        "bounds": {"south": 52.30, "north": 52.65, "west": 5.05, "east": 5.55},
    },
    {
        "id": "overijssel-gelderland",
        "name": "Overijssel & Gelderland",
        "bounds": {"south": 52.05, "north": 52.90, "west": 5.55, "east": 6.30},
    },
]


def slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "spot"


def extract_nkv_array(text: str) -> list[dict]:
    match = re.search(r'\[\{"titel"', text)
    if not match:
        raise ValueError("NKV spot JSON array not found in source file")
    start = match.start()
    depth = 0
    for index, char in enumerate(text[start:], start):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])
    raise ValueError("Unclosed JSON array in source file")


def assign_region(lat: float, lon: float) -> str:
    for region in REGIONS:
        bounds = region["bounds"]
        if (
            bounds["south"] <= lat <= bounds["north"]
            and bounds["west"] <= lon <= bounds["east"]
        ):
            return region["id"]
    return "other"


def pick_level(niveau: list[str]) -> str:
    mapped = [LEVEL_MAP.get(item, "advanced") for item in niveau]
    return min(mapped, key=lambda level: LEVEL_RANK[level])


def build() -> None:
    raw = extract_nkv_array(SOURCE.read_text())
    spots: list[dict] = []
    seen_slugs: set[str] = set()

    for entry in raw:
        if entry.get("cpt") != "kitespot":
            continue
        policy_raw = (entry.get("beleid") or [""])[0]
        if policy_raw not in ("toegestaan", "beperkt"):
            continue
        wind_raw = entry.get("windrichting") or []
        if not wind_raw:
            continue

        lat, lon = entry["lat_lng"]
        name = entry["titel"]
        spot_id = slugify(name)
        if spot_id in seen_slugs:
            suffix = 2
            while f"{spot_id}-{suffix}" in seen_slugs:
                suffix += 1
            spot_id = f"{spot_id}-{suffix}"
        seen_slugs.add(spot_id)

        spots.append(
            {
                "id": spot_id,
                "name": name,
                "lat": lat,
                "lon": lon,
                "region": assign_region(lat, lon),
                "policy": POLICY_MAP[policy_raw],
                "good_directions": [WIND_MAP[w] for w in wind_raw if w in WIND_MAP],
                "windrichtingen": wind_raw,
                "waterdiepte": entry.get("waterdiepte") or [],
                "niveau": entry.get("niveau") or ["Gevorderd"],
                "level": pick_level(entry.get("niveau") or ["Gevorderd"]),
                "openstelling": entry.get("openstelling", ""),
                "permalink": entry.get("permalink", ""),
            }
        )

    spots.sort(key=lambda spot: (spot["region"], spot["name"]))
    OUT_SPOTS.parent.mkdir(parents=True, exist_ok=True)
    OUT_SPOTS.write_text(json.dumps(spots, indent=2, ensure_ascii=False) + "\n")
    OUT_REGIONS.write_text(
        json.dumps(
            {
                "regions": REGIONS,
                "source": "NKV Spotkaart (toegestaan + beperkt spots only)",
                "spot_count": len(spots),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(f"Wrote {len(spots)} spots to {OUT_SPOTS}")


if __name__ == "__main__":
    build()
