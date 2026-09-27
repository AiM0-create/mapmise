"""Preparation outputs derived from the catalogue: per-window, per-band VRT mosaics.

A VRT is a tiny XML that stitches the AOI-clipped tile COGs of one month and band into one
layer, so QGIS shows "2026-06 red" instead of four tiles. Rebuilt from the catalogue on every
call; nothing is copied. Written directly (no gdalbuildvrt needed) with paths relative to the VRT,
so a project folder can be moved or zipped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from mapmise.catalog import Catalog


def build_vrts(cat: Catalog) -> list[Path]:
    """One VRT per (window, band) under <project>/data/vrt/. Returns the paths written."""
    groups: dict[tuple[str, str], list[Path]] = {}
    for iid in sorted(cat.item_ids()):
        it = cat.load_item(iid)
        window = it["properties"].get("mapmise:window", "unknown")
        for band, a in it["assets"].items():
            p = (cat.items_dir / a["href"]).resolve()
            if p.exists() and p.suffix == ".tif":
                groups.setdefault((f"{it['properties'].get('mapmise:source', 'x')}_{window}", band), []).append(p)
    out_dir = cat.root / "data" / "vrt"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for (window, band), files in sorted(groups.items()):
        vrt = out_dir / f"{window}_{band}.vrt"
        write_vrt(vrt, files)
        written.append(vrt)
    return written


_VRT_TYPES = {"uint8": "Byte", "int8": "Int8", "uint16": "UInt16", "int16": "Int16", "uint32": "UInt32", "int32": "Int32",
              "float32": "Float32", "float64": "Float64"}


def write_vrt(vrt: Path, files: list[Path]) -> None:
    """Mosaic of single-band rasters in one projection (every project file is in the project's projection),
    on the average pixel size of the inputs, as gdalbuildvrt does by default."""
    import rasterio
    infos = []
    for f in files:
        with rasterio.open(f) as ds:
            infos.append((f, ds.bounds, ds.res, ds.width, ds.height, ds.dtypes[0], ds.nodata, ds.crs))
    xres = sum(i[2][0] for i in infos) / len(infos)
    yres = sum(i[2][1] for i in infos) / len(infos)
    left = min(i[1].left for i in infos); right = max(i[1].right for i in infos)
    bottom = min(i[1].bottom for i in infos); top = max(i[1].top for i in infos)
    width, height = round((right - left) / xres), round((top - bottom) / yres)
    dtype, nodata, crs = infos[0][5], infos[0][6], infos[0][7]
    nd = f"<NoDataValue>{nodata!r}</NoDataValue>" if nodata is not None else ""
    sources = []
    for f, b, _res, w, h, *_ in infos:
        rel = Path(os.path.relpath(f, vrt.parent)).as_posix()
        sources.append(
            f'<ComplexSource><SourceFilename relativeToVRT="1">{escape(rel)}</SourceFilename><SourceBand>1</SourceBand>'
            f'<SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>'
            f'<DstRect xOff="{(b.left - left) / xres!r}" yOff="{(top - b.top) / yres!r}" xSize="{(b.right - b.left) / xres!r}" ySize="{(b.top - b.bottom) / yres!r}"/>'
            + (f"<NODATA>{nodata!r}</NODATA>" if nodata is not None else "") + "</ComplexSource>")
    vrt.write_text(
        f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">\n'
        f"<SRS>{escape(crs.to_wkt())}</SRS>\n"
        f"<GeoTransform>{left!r}, {xres!r}, 0.0, {top!r}, 0.0, {-yres!r}</GeoTransform>\n"
        f'<VRTRasterBand dataType="{_VRT_TYPES.get(dtype, "Float64")}" band="1">{nd}\n' + "\n".join(sources) + "\n</VRTRasterBand>\n</VRTDataset>\n", encoding="utf-8")


def vector_files(cat: Catalog) -> list[Path]:
    out = []
    for iid in sorted(cat.item_ids()):
        for a in cat.load_item(iid)["assets"].values():
            p = (cat.items_dir / a["href"]).resolve()
            if p.exists() and p.suffix == ".gpkg":
                out.append(p)
    return out


def find_qgis() -> list[str] | None:
    """The command that starts QGIS on this computer: on PATH, or in the standard install locations
    (Windows: the OSGeo4W / standalone installers; macOS: /Applications)."""
    for name in ("qgis", "qgis-ltr", "qgis-bin", "qgis-ltr-bin"):
        if exe := shutil.which(name):
            return [exe]
    if sys.platform.startswith("win"):
        roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")] + ["C:\\OSGeo4W", "C:\\OSGeo4W64"]
        for root in filter(None, roots):
            for pattern in ("QGIS*/bin/qgis-ltr-bin.exe", "QGIS*/bin/qgis-bin.exe", "bin/qgis-ltr-bin.exe", "bin/qgis-bin.exe"):
                hits = sorted(Path(root).glob(pattern), reverse=True)  # newest version first
                if hits:
                    return [str(hits[0])]
    if sys.platform == "darwin":
        for app in sorted(Path("/Applications").glob("QGIS*.app"), reverse=True):
            return ["open", "-a", str(app), "--args"]
    return None


def open_in_qgis(files: list[Path]) -> bool:
    """Launch QGIS detached with the given layers. Returns False if QGIS is not installed where we can find it."""
    cmd = find_qgis()
    if not cmd:
        return False
    kw = {"creationflags": subprocess.DETACHED_PROCESS} if sys.platform.startswith("win") else {"start_new_session": True}
    subprocess.Popen([*cmd, *map(str, files)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    return True
