# geofetch (prototype)

Project-first, reproducible Earth-observation data acquisition. You describe an area, a period and an objective; it tells you which scenes you need and why, whether the period is even feasible, how much it will transfer, and — after you approve — fetches only the AOI window of each asset into a local static STAC catalogue that QGIS opens directly.

Status: **prototype validating a hypothesis**, not a product. One sensor (Sentinel-2 L2A), one provider (Earth Search, anonymous), no GUI, no LLM. The objective layer is a deterministic rule table (`geofetch/objectives.py`): four templates, each listing the data it needs, why, and whether this prototype can actually fetch it. Read `docs/PRODUCT_DISCOVERY.md` for why, `docs/EXPERIMENTS.md` for the evidence.

## Install

Requires Python ≥ 3.12 and network access to `earth-search.aws.element84.com`.

```bash
uv venv --python 3.12 .venv && uv pip install -e . && uv pip install pytest
```

## Use

```bash
geofetch init myproj --name "Chitradurga Drought 2026" --aoi district.geojson --start 2026-06-01 --end 2026-09-15 --objective "Assess agricultural drought during the 2026 monsoon"
geofetch requirements myproj          # objective → data requirements, with reasons and support status
geofetch plan myproj                  # plans the first supported required requirement (flags override)
geofetch plan myproj --requirement optical_pre_post --event 2026-08-15   # pre/post templates need an event date
geofetch show myproj                  # list plans and per-month verdicts
geofetch run  myproj                  # shows size, asks for approval, fetches, records
geofetch status myproj                # per requirement: acquired / planned / not acquirable — "what am I missing?"
geofetch open myproj                  # per-month VRT mosaics, launched in QGIS with the AOI
geofetch run  myproj --mode composite # fetch the multi-scene composite set instead
```

`init` matches the objective text to a template by keywords (`drought`, `flood`, `ndvi`, `reservoir`…); `--template` overrides; an ambiguous or unmatched objective asks you to choose rather than guessing.

What a plan tells you, per month: scenes available; coverage and expected clear fraction for one-scene-per-tile; how many scenes a composite needs to reach the clear target; what a naive `cloud ≤ N%` filter would have returned; and a verdict — `feasible-single`, `feasible-composite`, `infeasible` (no combination of scenes reaches the target: consider SAR or a longer window), or `incomplete` (tiles with no acquisition).

## Project layout

```
myproj/
  project.json               objective, period, AOI reference, project CRS (UTM of AOI centroid)
  aoi/aoi.geojson
  plans/<id>.json            plan + parameters + query record + estimate + execution log (the acquisition record)
  catalog/catalog.json       static STAC — open in QGIS ≥ 3.40: Browser → STAC → add catalog.json
  catalog/items/<scene>.json one Item per scene, sha256 per band, source hrefs
  data/<collection>/<YYYY-MM>/<scene>_<band>.tif   AOI-clipped COGs in the project CRS
  .cache/                    catalogue search pages and asset sizes; safe to delete
```

## Design rules (from the discovery)

- Deterministic planning; nothing is downloaded on a suggestion. A plan is data you can read, edit, and approve.
- Reuse: pystac-client (search), GDAL/rasterio (windowed reads, COG), Shapely/pyproj (geometry). No custom downloader; no aria2.
- Windowed transfer reads only the AOI blocks of each COG, in parallel — pixel-identical to a full download, and faster once parallel (E2).
- Everything acquired is described in the catalogue with checksums and source hrefs, so "how did I get this?" is always answerable.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Planner tests run offline against a recorded catalogue page; transfer/catalogue tests use a synthetic local raster.
