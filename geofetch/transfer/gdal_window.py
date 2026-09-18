"""GDALWindowTransfer: fetch only the AOI window of a remote COG.

Uses the system `gdalwarp` over GDAL's /vsicurl/ virtual filesystem, so only the
HTTP byte ranges covering the cutline are transferred. Bytes actually downloaded
are measured by parsing GDAL's VSICURL debug log — nothing is estimated here.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",  # no directory listing / sidecar probing
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "CPL_DEBUG": "ON",
}

_RANGE = re.compile(r"Downloading (\d+)-(\d+)")


@dataclass
class TransferResult:
    href: str
    output: Path
    bytes_downloaded: int
    http_ranges: int
    seconds: float
    sha256: str
    output_bytes: int
    log: list[str] = field(default_factory=list)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def clip_cog(
    href: str,
    cutline: Path,
    output: Path,
    *,
    t_srs: str | None = None,
    creation_options: tuple[str, ...] = ("COMPRESS=DEFLATE", "PREDICTOR=2", "TILED=YES"),
    extra_env: dict[str, str] | None = None,
) -> TransferResult:
    """Clip `href` (http(s) or local path) to `cutline`, writing a GeoTIFF.

    When `href` is remote it is wrapped in /vsicurl/. Without `t_srs` no resampling
    occurs, so pixel values inside the cutline are identical to the source.
    """
    src = f"/vsicurl/{href}" if href.startswith("http") else href
    cmd = ["gdalwarp", "-overwrite", "-q", "-cutline", str(cutline), "-crop_to_cutline", "-of", "GTiff"]
    if t_srs:
        cmd += ["-t_srs", t_srs]
    for co in creation_options:
        cmd += ["-co", co]
    cmd += [src, str(output)]
    env = {**os.environ, **GDAL_ENV, **(extra_env or {})}
    t0 = time.time()
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    seconds = time.time() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"gdalwarp failed ({proc.returncode}): {proc.stderr[-2000:]}")
    ranges = [(int(a), int(b)) for a, b in _RANGE.findall(proc.stderr)]
    downloaded = sum(b - a + 1 for a, b in ranges)
    return TransferResult(
        href=href, output=output, bytes_downloaded=downloaded, http_ranges=len(ranges),
        seconds=seconds, sha256=sha256_of(output), output_bytes=output.stat().st_size,
        log=[ln for ln in proc.stderr.splitlines() if "VSICURL" in ln][:5],
    )
