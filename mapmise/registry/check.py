"""Live check of registry entries — the gate for community contributions.

For each source: can we reach it the way the entry says, and do the declared assets exist?
  stac      a search over the check area returns items, and every declared asset key is present
            on the first item and answers a HEAD request (signed where needed)
  http      each templated URL answers (HEAD, or GET for JSON APIs)
  overpass  a tiny query runs
  gdacs     the event list answers
An entry may override the probe area/time with `check: {bbox: [...], start: YYYY-MM-DD, end: YYYY-MM-DD, iso3: XXX}`.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from mapmise.drivers import gdacs, overpass
from mapmise.drivers import http as http_driver
from mapmise.drivers import stac as stac_driver
from mapmise.drivers.signing import sign
from mapmise.geo import UA
from mapmise.registry import Source

DEFAULT_BBOX = [76.3, 14.1, 76.5, 14.3]  # small area in Karnataka, India
DEFAULT_ISO3 = "IND"


@dataclass
class CheckResult:
    source: str
    ok: bool
    detail: str


def _head_ok(url: str) -> tuple[bool, str]:
    from mapmise.drivers.signing import earthdata_resolve, needs_earthdata
    if needs_earthdata(url):  # NASA protected archive: an authenticated 1-byte read proves access
        from mapmise.auth import EarthdataLoginRequired
        try:
            _, size = earthdata_resolve(url)
            return True, f"readable with your Earthdata token ({size:,} bytes)" if size else "readable with your Earthdata token"
        except EarthdataLoginRequired:
            return True, "download not checked — add an Earthdata token to check it"
        except (PermissionError, httpx.HTTPError) as e:
            return False, str(e)
    try:
        r = httpx.head(sign(url), headers=UA, timeout=60, follow_redirects=True)
        if r.status_code in (405, 403):  # some hosts refuse HEAD: try a 1-byte range GET
            r = httpx.get(sign(url), headers={**UA, "Range": "bytes=0-0"}, timeout=60, follow_redirects=True)
        return r.status_code in (200, 206), f"HTTP {r.status_code}"
    except httpx.HTTPError as e:
        return False, type(e).__name__


def _period(s: Source, c: dict) -> tuple[str | None, str | None]:
    if s.shape != "series":
        return None, None
    if c.get("start") and c.get("end"):
        return c["start"], c["end"]
    to = s.temporal.get("to")
    if to:  # ended series: probe its last year
        y = str(to)[:4]
        return f"{y}-01-01", f"{y}-12-31"
    return "2025-01-01", "2025-12-31"


def check_source(s: Source) -> CheckResult:
    c = s.access.get("check", {})
    bbox = c.get("bbox", DEFAULT_BBOX)
    iso3 = c.get("iso3", DEFAULT_ISO3)
    a = s.access
    try:
        if s.driver == "stac":
            start, end = _period(s, c)
            assets = list(a["assets"])
            items, _ = stac_driver.search(s, stac_driver.build_query(s, bbox, start, end, assets))
            if not items:
                return CheckResult(s.id, False, f"no items for bbox {bbox}" + (f" {start}…{end}" if start else ""))
            missing = [k for k in assets if k not in items[0].assets]
            if missing:
                return CheckResult(s.id, False, f"{len(items)} items, but assets missing on {items[0].id}: {missing}")
            ok, why = _head_ok(items[0].assets[assets[0]]["href"])
            return CheckResult(s.id, ok, f"{len(items)} items; asset '{assets[0]}' {why}")
        if s.driver == "http":
            mode = a.get("mode")
            if mode == "vector":
                r = httpx.get(http_driver.render_url(a["url"], iso3=iso3), headers=UA, timeout=60, follow_redirects=True)
                ok = r.status_code == 200 and (not a.get("url_key") or a["url_key"] in r.json())
                return CheckResult(s.id, ok, f"HTTP {r.status_code}" + (f", key '{a['url_key']}' present" if ok and a.get("url_key") else ""))
            if mode == "tile_grid":
                step = a["tile_deg"]
                import math
                top, left = math.ceil(bbox[3] / step) * step, math.floor(bbox[0] / step) * step
                tile = f"{abs(top):02d}{'N' if top >= 0 else 'S'}_{abs(left):03d}{'E' if left >= 0 else 'W'}"
                res = [(k, *_head_ok(a["url"].format(tile=tile, layer=layer))) for k, layer in a["assets"].items()]
                return CheckResult(s.id, all(r[1] for r in res), f"tile {tile}: " + ", ".join(f"{k} {w}" for k, _, w in res))
            if mode == "file_per_window":
                start, _ = _period(s, c)
                url = http_driver.render_url(a["url"], yyyy=start[:4], mm=start[5:7])
                ok, why = _head_ok(url)
                return CheckResult(s.id, ok, f"{start[:7]} file {why}")
            urls = a.get("urls") or {k: a["url"] for k in a["assets"]}
            res = [(k, *_head_ok(http_driver.render_url(u, iso3=iso3))) for k, u in urls.items()]
            return CheckResult(s.id, all(r[1] for r in res), ", ".join(f"{k} {w}" for k, _, w in res))
        if s.driver == "overpass":
            tiny = [bbox[0], bbox[1], bbox[0] + 0.02, bbox[1] + 0.02]
            els = overpass.run_query(overpass.build_query(s, tiny))
            return CheckResult(s.id, True, f"query ran, {len(els)} elements in a 2 km box")
        if s.driver == "gdacs":
            code = next(iter(a["types"].values()))
            evs = gdacs.events(code, [-180, -90, 180, 90], "2025-01-01", "2025-12-31", radius_deg=400)
            return CheckResult(s.id, True, f"event list answered ({len(evs)} {code} events in 2025)")
    except Exception as e:  # noqa: BLE001 — a check reports, never raises
        return CheckResult(s.id, False, f"{type(e).__name__}: {str(e)[:140]}")
    return CheckResult(s.id, False, f"no check for driver {s.driver}")
