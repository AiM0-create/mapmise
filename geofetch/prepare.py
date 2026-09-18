"""Preparation outputs derived from the catalogue: per-window, per-band VRT mosaics.

A VRT is a tiny XML that stitches the AOI-clipped tile COGs of one month and band into one
layer, so QGIS shows "2026-06 red" instead of four tiles. Rebuilt from the catalogue on every
call; nothing is copied.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from geofetch.catalog import Catalog


def build_vrts(cat: Catalog) -> list[Path]:
    """One VRT per (window, band) under <project>/data/vrt/. Returns the paths written."""
    groups: dict[tuple[str, str], list[Path]] = {}
    for iid in sorted(cat.item_ids()):
        it = cat.load_item(iid)
        window = it["properties"].get("geofetch:window", "unknown")
        for band, a in it["assets"].items():
            p = (cat.items_dir / a["href"]).resolve()
            if p.exists():
                groups.setdefault((window, band), []).append(p)
    out_dir = cat.root / "data" / "vrt"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for (window, band), files in sorted(groups.items()):
        vrt = out_dir / f"{window}_{band}.vrt"
        subprocess.run(["gdalbuildvrt", "-q", "-overwrite", str(vrt), *map(str, files)], check=True)
        written.append(vrt)
    return written


def open_in_qgis(files: list[Path]) -> bool:
    """Launch QGIS detached with the given layers. Returns False if qgis is not on PATH."""
    try:
        subprocess.Popen(["qgis", *map(str, files)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        return False
