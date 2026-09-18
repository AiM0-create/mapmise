"""Transfer size estimates from real asset sizes.

Earth Search items carry no `file:size`, so sizes come from one HEAD per asset
(cached per project). Windowed estimate per asset = size × (tile∩AOI / tile) + fixed
overhead, calibrated in docs/EXPERIMENTS.md E2b (within ~7 %, ~2 MB header/IFD/overview cost).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from geofetch.discovery.stac import NormalisedItem

WINDOW_OVERHEAD_BYTES = 2_000_000


def asset_sizes(items: list[NormalisedItem], bands: list[str], cache_dir: Path | None, workers: int = 16) -> dict[str, int]:
    """Return {item_id/band: bytes}. Uses `file:size` when the catalogue provides it, else HEAD."""
    cache_file = cache_dir / "asset_sizes.json" if cache_dir else None
    sizes: dict[str, int] = json.loads(cache_file.read_text()) if cache_file and cache_file.exists() else {}
    todo = []
    for it in items:
        for b in bands:
            a = it.assets.get(b)
            if not a:
                continue
            key = f"{it.id}/{b}"
            if key in sizes:
                continue
            if a.get("file:size"):
                sizes[key] = int(a["file:size"])
            else:
                todo.append((key, a["href"]))

    def head(t):
        key, href = t
        r = httpx.head(href, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return key, int(r.headers["content-length"])

    if todo:
        with ThreadPoolExecutor(workers) as ex:
            for key, n in ex.map(head, todo):
                sizes[key] = n
        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(sizes))
    return sizes


def estimate(items_by_id: dict[str, NormalisedItem], item_ids: list[str], bands: list[str],
             tile_fraction: dict[str, float], sizes: dict[str, int]) -> dict[str, int]:
    full = windowed = 0
    for iid in item_ids:
        it = items_by_id[iid]
        for b in bands:
            n = sizes.get(f"{iid}/{b}")
            if n is None:
                continue
            full += n
            windowed += int(n * tile_fraction.get(it.tile, 1.0)) + WINDOW_OVERHEAD_BYTES
    return {"assets": len(item_ids) * len(bands), "full_bytes": full, "windowed_bytes": windowed}
