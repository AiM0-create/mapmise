"""`mapmise selftest`: checks that this installation works on this computer.

Each check exercises the real code path, not a mock: the registry, the built-in AI, PROJ, vector writing,
the library database, the mosaic writer and — unless --offline — a real clipped download of a small
Copernicus DEM window over HTTPS. Used by the release builds on every operating system, and useful to
anyone whose installation misbehaves.
"""

from __future__ import annotations

import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from shapely.geometry import box

# a 2 km square in Karnataka, inside Copernicus DEM tile N12 E076
_AOI = box(76.40, 12.30, 76.418, 12.318)
_DEM = "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N12_00_E076_00_DEM/Copernicus_DSM_COG_10_N12_00_E076_00_DEM.tif"


def _registry(tmp: Path) -> str:
    from mapmise.registry import load_ask_rules, load_sources
    s, r = load_sources(), load_ask_rules()
    assert len(s) >= 20 and len(r) >= 10, f"only {len(s)} sources and {len(r)} rules"
    return f"{len(s)} sources, {len(r)} ask rules"


def _ai(tmp: Path) -> str:
    from mapmise.ai.understand import match
    from mapmise.registry import load_ask_rules
    m = match("the tank near our village dried up completely this summer", load_ask_rules(), set())
    assert m, "the built-in model understood nothing"
    return f"understood a keyword-free question as {[x.rule for x in m]}"


def _proj(tmp: Path) -> str:
    from pyproj import Transformer
    x, y = Transformer.from_crs(4326, 32643, always_xy=True).transform(76.4, 12.3)
    assert abs(x - 651_000) < 5_000 and abs(y - 1_360_000) < 5_000, (x, y)
    return "EPSG:4326 → UTM 43N"


def _vector(tmp: Path) -> str:
    import geopandas as gpd
    f = tmp / "aoi.gpkg"
    gpd.GeoDataFrame({"name": ["test"]}, geometry=[_AOI], crs="EPSG:4326").to_file(f, driver="GPKG")
    assert len(gpd.read_file(f)) == 1
    return "GeoPackage written and read"


def _library(tmp: Path) -> str:
    from mapmise.library import Library
    lib = Library(tmp / "library.sqlite")
    lib.record(path=tmp / "x.tif", project=tmp, source="s", item="i", asset="a", window=None, date=None, epsg=32643,
               native_grid=True, cover=_AOI, bytes_=1, sha256="0", source_href=None)
    n = lib.summary()["files"]
    lib.close()
    assert n == 1
    return "SQLite index"


def _download(tmp: Path) -> str:
    from mapmise.prepare import write_vrt
    from mapmise.transfer.window import fetch_window
    import rasterio
    out = tmp / "dem.tif"
    r = fetch_window(_DEM, _AOI, out, 32643, threads=4)
    with rasterio.open(out) as ds:
        a = ds.read(1)
        assert ds.crs.to_epsg() == 32643 and a.size > 1000 and 300 < float(a.mean()) < 1500, (ds.crs, a.size, float(a.mean()))
    vrt = tmp / "dem.vrt"
    write_vrt(vrt, [out])
    with rasterio.open(vrt) as ds:
        assert ds.read(1).shape == a.shape
    return f"{r.width}×{r.height} px clipped from Copernicus DEM in {r.seconds:.1f} s, mosaic readable"


CHECKS: list[tuple[str, Callable[[Path], str], bool]] = [
    ("data registry", _registry, False),
    ("built-in AI", _ai, False),
    ("projections (PROJ)", _proj, False),
    ("vector files (GDAL/OGR)", _vector, False),
    ("personal library", _library, False),
    ("clipped download (GDAL over HTTPS)", _download, True),
]


TIME_LIMIT_S = 180


def _print(line: str) -> None:
    print(line, flush=True)


def run(offline: bool = False, echo: Callable[[str], None] = _print) -> bool:
    ok = True
    with tempfile.TemporaryDirectory(prefix="mapmise-selftest-", ignore_cleanup_errors=True) as d:
        for name, fn, needs_net in CHECKS:
            if needs_net and offline:
                echo(f"  -    {name}: skipped (--offline)")
                continue
            echo(f"  ...  {name}")
            t0 = time.time()
            result: dict = {}

            def attempt(fn=fn):
                try:
                    result["detail"] = fn(Path(d))
                except Exception as e:  # noqa: BLE001 — report every failure, keep checking
                    result["error"] = f"{type(e).__name__}: {e}"
                    result["trace"] = traceback.format_exc()

            t = threading.Thread(target=attempt, daemon=True)
            t.start()
            t.join(TIME_LIMIT_S)
            if t.is_alive():
                ok = False
                echo(f"  FAIL {name}: no answer after {TIME_LIMIT_S} s")
            elif "error" in result:
                ok = False
                echo(f"  FAIL {name}: {result['error']}")
                echo("       " + result["trace"].strip().replace("\n", "\n       "))
            else:
                echo(f"  ok   {name}: {result['detail']} ({time.time() - t0:.1f} s)")
    echo("all checks passed" if ok else "some checks failed — please report them with the output above")
    return ok
