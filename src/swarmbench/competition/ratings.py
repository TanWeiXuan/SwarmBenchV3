"""Current-only V3 rating state with atomic serialization."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .glicko2 import GlickoRating, simultaneous_update

RATINGS_SCHEMA_VERSION = 3


@dataclass(frozen=True, slots=True)
class RatingRecord:
    controller_id: str
    display_name: str
    author: str
    rating: float = 1500.0
    deviation: float = 350.0
    volatility: float = 0.06
    wins: int = 0
    draws: int = 0
    losses: int = 0
    games: int = 0
    version_sha: str = ""
    built_in: bool = False

    @property
    def glicko(self) -> GlickoRating:
        return GlickoRating(self.rating, self.deviation, self.volatility)


def ratings_to_dict(records: dict[str, RatingRecord]) -> dict[str, Any]:
    return {"schema_version": RATINGS_SCHEMA_VERSION, "controllers": [asdict(records[key]) for key in sorted(records)]}


def save_ratings(records: dict[str, RatingRecord], path: str | Path) -> None:
    destination = Path(path)
    payload = json.dumps(ratings_to_dict(records), indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, destination)


def load_ratings(path: str | Path) -> dict[str, RatingRecord]:
    source = Path(path)
    if source.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("ratings file is too large")
    data = json.loads(source.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid numeric constant: {value}")))
    if data.get("schema_version") != RATINGS_SCHEMA_VERSION or not isinstance(data.get("controllers"), list):
        raise ValueError("invalid V3 ratings schema")
    records = {}
    for item in data["controllers"]:
        if not isinstance(item, dict):
            raise ValueError("invalid rating record")
        record = RatingRecord(**item)
        if (
            record.controller_id in records
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?", record.controller_id)
            or any(type(value) is not int or value < 0 for value in (record.wins, record.draws, record.losses, record.games))
            or record.games != record.wins + record.draws + record.losses
            or any(type(value) not in {int, float} for value in (record.rating, record.deviation, record.volatility))
            or not all(math.isfinite(value) for value in (record.rating, record.deviation, record.volatility))
            or not (0 < record.deviation <= 350 and 0 < record.volatility < 2 and -10_000 < record.rating < 10_000)
            or not isinstance(record.display_name, str)
            or not isinstance(record.author, str)
            or type(record.built_in) is not bool
            or not isinstance(record.version_sha, str)
            or len(record.display_name) > 200
            or len(record.author) > 100
            or len(record.version_sha) > 64
        ):
            raise ValueError("invalid or duplicate rating record")
        records[record.controller_id] = record
    return records


def apply_rating_period(records: dict[str, RatingRecord], games: list[tuple[str, str, float]]) -> dict[str, RatingRecord]:
    updated = simultaneous_update({key: record.glicko for key, record in records.items()}, games)
    counters = {key: [0, 0, 0] for key in records}
    for left, right, score in games:
        if score == 1.0:
            counters[left][0] += 1; counters[right][2] += 1
        elif score == 0.0:
            counters[right][0] += 1; counters[left][2] += 1
        else:
            counters[left][1] += 1; counters[right][1] += 1
    return {key: replace(record, rating=updated[key].rating, deviation=updated[key].deviation, volatility=updated[key].volatility, wins=record.wins + counters[key][0], draws=record.draws + counters[key][1], losses=record.losses + counters[key][2], games=record.games + sum(counters[key])) for key, record in records.items()}
