# geofetch (alpha prototype)

**You say what you want to analyse and where. It finds the open data that analysis needs, tells you why and whether it's feasible, fetches only your area, organises and dates it, and writes down exactly how — so anyone can reproduce it.**

Raster and vector, from the open community's catalogues and APIs. No pre-processing: if an analysis-ready product exists openly, that's what you get. Everything runs locally and ends up in a folder that QGIS opens directly.

```bash
geofetch init  ~/chitra --name "Chitradurga" --aoi district.geojson --start 2026-06-01 --end 2026-09-15
geofetch ask   ~/chitra "I heard a flood happened here in August, help me analyse it locally" --event 2026-08-15
```

```
need                         source                     files     est.  verdicts
elevation/static             cop-dem-glo-30                 4    36 MB  static=single
sar/pair                     sentinel-1-rtc                 8  1.40 GB  pre=single, post=single
water/static                 jrc-gsw-occurrence             3     7 MB  static=single
optical/pair                 sentinel-2-l2a                32  1.51 GB  pre=infeasible, post=composite
population/static            worldpop-constrained-2020      1  0.53 GB  static=single
hydrography/static           osm-waterways                  1     live  static=single
transport/static             osm-roads                      1     live  static=single

total: 7 sources, 50 files, ≈ 3.49 GB to transfer
Why each dataset: …          Verdicts needing your attention: …
fetch 7 source(s)? [y/N]
```

Nothing is transferred on a suggestion: the plan is shown with reasons and sizes, and you approve it.

## How it works — three data files and a small engine

The engine knows nothing about floods, droughts or cities. All domain knowledge is in editable data:

| file | what it holds | who edits it |
|---|---|---|
| `geofetch/registry/sources.yaml` | open datasets described by *what they are*: themes, raster/vector, data shape, resolution, time span, coverage, licence, analysis-ready, and how to access them (one of four drivers) | the community — add a dataset, no code |
| `geofetch/registry/asks.yaml` | keyword rules that turn words into generic *needs* (theme + static/series/before-after + priority + why). Rules compose: any ask is the union of the rules it matches | the community — add vocabulary, no code |
| your project folder | AOI, period, requests, plans, catalogue, report | you |

Engine, in order: **resolver** (ask → needs → best source per need, with alternatives and reasons) → **planners by data shape** (dated scenes: cloud-aware per tile, or radar per orbit; static layers; whole-file series; vector queries) → **executor** (windowed range reads of cloud-optimised rasters in parallel, whole-file with resume where servers don't support ranges, live vector queries; clip, project, COG) → **catalogue** (static STAC with SHA-256 and source URLs) → **REPORT.md**.

Drivers: `stac` (Earth Search, Planetary Computer with automatic signing), `http` (file trees, per-country files, JSON APIs), `overpass` (OpenStreetMap), `gdacs` (disaster events, to turn "the flood in X" into a date).

## Commands

```
geofetch init   <dir> --name --aoi --start --end [--objective]
geofetch ask    <dir> "…" [--event YYYY-MM-DD] [--dry-run] [--yes] [--skip theme:temporal] [--use theme:temporal=source] [--mode composite]
geofetch run    <dir> [request-id]      execute a request later / resume
geofetch status <dir>                   what the project has, per need; what is missing and why
geofetch open   <dir>                   mosaics + vectors + AOI in QGIS
geofetch report <dir>                   REPORT.md — the acquisition record
geofetch sources [--theme t]            the registry
```

Verdicts per time window: `feasible-single`, `feasible-composite` (needs N scenes to reach the clear-coverage target), `infeasible` (no combination of scenes reaches it — the plan says what to use instead), `incomplete` (no coverage, or a query too large for the AOI).

## Project layout

```
myproj/
  project.json        AOI, period, country, project CRS
  requests/<id>.json  the ask, needs, chosen sources with alternatives, unmet needs and why
  plans/<id>.json     one per source: query, selected items with source URLs, verdicts, estimate, execution log with SHA-256
  catalog/            static STAC (open in QGIS ≥ 3.40: Browser → STAC → catalog.json)
  data/<source>/<window>/…   AOI-clipped COGs in the project CRS; GeoPackages for vectors
  REPORT.md           human-readable record
  .cache/             search pages, sizes, whole-file downloads; safe to delete
```

## Registry (13 sources at alpha)

Sentinel-2 L2A · Sentinel-1 RTC · Copernicus DEM 30 m · ESA WorldCover · JRC Global Surface Water · IO annual land cover · WorldPop · CHIRPS monthly rainfall · geoBoundaries · OSM roads / buildings / waterways · GDACS events. `geofetch sources` lists them. Adding one is a YAML entry — see the comments at the top of `sources.yaml`.

## Install & test

Python ≥ 3.12.

```bash
uv venv --python 3.12 .venv && uv pip install -e . && uv pip install pytest && .venv/bin/python -m pytest -q
```

## Status and limits

Alpha. Validated end to end on real data (see `docs/EXPERIMENTS.md`). Known limits: keyword matching, not language understanding (an ask that matches no rule tells you so); event dates come from GDACS or you, never guessed; OSM building queries are capped by area; some servers (WorldPop) ignore range requests so the country file is downloaded once; no cloud masking or compositing — only selection, with honest verdicts. Design rationale: `docs/PRODUCT_DISCOVERY.md`.
