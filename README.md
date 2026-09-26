# geofetch

**Say what you want to analyse and where. geofetch works out which open geospatial data that needs, checks whether it is actually usable for your area and period, fetches only your area, organises and dates it, and writes down exactly how — ready for QGIS or Python.**

```bash
geofetch ask "I heard a flood happened in Chitradurga in August 2026, help me analyse it locally"
```

```
understood:
  place   “Chitradurga” → Chitradurga, Karnataka, India (boundary/administrative, relation/2020623)
          8,434 km², IND, project CRS EPSG:32643
  period  2026-08-01 → 2026-08-31  ← “august 2026”
  asks    flood → 9 data needs
  before/after  no exact event date known (GDACS has none near here) — using the stated period as 'after'
                and the 30 days before it as 'before'. Pass --event YYYY-MM-DD to be precise

need                         source                         files     est.  verdicts
elevation/static             cop-dem-glo-30                     4    36 MB  static=single
sar/pair                     sentinel-1-rtc                     8  1.08 GB  pre=single, post=single
water/static                 jrc-gsw-occurrence                 6    12 MB  static=single
optical/pair                 sentinel-2-l2a                    16  1.45 GB  pre=infeasible, post=composite
population/static            worldpop-constrained-2020          1  0.53 GB  static=single
facilities/static            osm-facilities                     1     live  static=single
transport/static             osm-roads                          1     live  static=single
…
Why each dataset:
  sar/pair (required) ← sentinel-1-rtc: floods happen under cloud; radar before/after the event shows new open water
  …
Verdicts needing your attention:
  sentinel-2-l2a pre: all 19 scenes combined reach only 70% expected clear (target 80%) …

fetch 9 source(s) into ./chitradurga-2026-08? [y/N]
```

Nothing is downloaded until you approve the plan.

> **Alpha.** Works end to end on real data today. Understanding is rule-based (no language model yet), and the registry covers 24 sources. Expect rough edges — and please report them.

## Why this exists

Every analysis starts with the same day of work: decide what data you need, find it across catalogues that all behave differently, discover that half of it is clouded out, fetch it, clip it, reproject it, name it sensibly, and try to remember how you got it. Experts do this on autopilot and still lose the day. Newcomers get it wrong. geofetch does that day for you — and keeps the record.

It is deliberately **not** an analysis tool. It never computes indices, masks clouds or classifies anything. If an open, analysis-ready product exists (radiometrically terrain-corrected radar, composited NDVI, land cover, surface water occurrence), you get that product.

## What makes it different

Plenty of excellent tools give you *access* to data: [EODAG](https://github.com/CS-SI/eodag), [earthlens](https://github.com/serapeum-org/earthlens), [pystac-client](https://github.com/stac-utils/pystac-client), the QGIS STAC browser, Copernicus Browser. You tell them which dataset; they fetch it. geofetch sits one step earlier and one step later:

- **From the analysis to the data.** "Flood" means radar before and after, a DEM, normal water extent, population, roads — each with a stated reason. Rules are plain YAML anyone can extend.
- **Feasibility before download.** Per tile and per month it picks the clearest scene and says when a period is *infeasible* for optical data — then adds a cloud-independent alternative automatically. When one product does not cover all your years, it stitches in another that does, and tells you which years nothing covers.
- **Only your area.** Cloud-optimised files are read in parallel windows; a 30 km area out of a 110 km Sentinel-2 tile transfers ~12× less.
- **The record is the output.** Every file lands in a static STAC catalogue with its SHA-256, source URL, query and the reason it was chosen, plus a human-readable `REPORT.md`.

## Install

Python ≥ 3.12. The alpha is installed from source:

```bash
git clone <this repository> geofetch && cd geofetch
uv venv --python 3.12 .venv && uv pip install -e .      # or: python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/geofetch --version
```

GDAL comes bundled with the rasterio wheel; no separate install is needed. QGIS is optional (for `geofetch open`).

## Use

```bash
geofetch ask "Assess agricultural drought in Chitradurga during the 2026 monsoon"
geofetch ask "Urban expansion of Bengaluru between 2005 and 2025" --dry-run
geofetch ask "Forest loss in Wayanad since 2015"
geofetch ask "Hospital and school access in Tumakuru district"
geofetch ask "Urban heat in Bengaluru in April 2026"
```

Place and dates are read from the sentence and shown back to you. Override anything:

| flag | effect |
|---|---|
| `--place "Tumakuru, Karnataka"`, `--pick 1` | geocode a different name, or take the next match |
| `--aoi area.gpkg` | use your own boundary file instead of a place name |
| `--start 2026-06-01 --end 2026-09-30` | set the period |
| `--event 2026-08-15` | exact event date for before/after data |
| `--skip buildings:static` | drop a need |
| `--use vegetation:series=modis-ndvi-16day` | force a particular source for a need |
| `--mode composite` | fetch enough optical scenes per window to reach the clear-coverage target |
| `--project DIR` | where the project lives (reused if it exists — asks accumulate in one project) |
| `--dry-run`, `--yes` | plan only / skip the approval prompt |

Then:

```bash
geofetch status  ./chitradurga-2026-08    # what the project holds, what is missing and why
geofetch run     ./chitradurga-2026-08    # resume or retry — completed files are skipped
geofetch open    ./chitradurga-2026-08    # per-period mosaics, vectors and the AOI in QGIS
geofetch report  ./chitradurga-2026-08    # regenerate REPORT.md
geofetch sources [--theme water]          # the data registry
geofetch rules                            # the words geofetch understands
```

### Reading a plan

Each source gets a verdict per time window:

| verdict | meaning |
|---|---|
| `single` | one scene (or product) per tile covers the area with enough clear view |
| `composite` | clear view needs several scenes; the plan says how many (`--mode composite` fetches them) |
| `infeasible` | no combination of available scenes reaches the clear-view target — typically monsoon optical; a cloud-independent fallback is added where one exists |
| `incomplete` | the area is not fully imaged in that window, a file is not published yet, or a query is too large |
| `out-of-range` | the product's record does not reach that year; another source is stitched in where one exists |

Periods longer than ~18 months are planned per year (one clear scene per tile per year); shorter ones per month. Before/after needs use an event date from `--event`, from the sentence, from GDACS, or — stated plainly — the requested period as "after".

## What you get

```
chitradurga-2026-08/
  project.json        area, period, country, projection; where the boundary came from
  aoi/aoi.geojson
  requests/<id>.json  what you asked, the needs, the sources chosen (with alternatives), what could not be met
  plans/<id>.json     per source: the catalogue query, every selected item and its URL, verdicts, estimate,
                      and the execution log with SHA-256 per file
  catalog/            static STAC catalogue — QGIS ≥ 3.40: Browser → STAC → add catalog/catalog.json
  data/<source>/<window>/<item>_<band>.tif   area-clipped Cloud-Optimised GeoTIFFs in one UTM projection
  data/<source>/static/<source>_<layer>.gpkg vectors, clipped
  data/vrt/           per-period mosaics built by `geofetch open`
  REPORT.md           the acquisition record in plain language
  .cache/             searches, sizes, whole-file downloads; safe to delete
```

Rasters are reprojected with nearest-neighbour resampling, so every output value exists in the source; nothing is interpolated.

## Data registry (alpha)

| theme | sources |
|---|---|
| optical | Sentinel-2 L2A (10 m, 2015–), Landsat 4–9 C2 L2 (30 m, 1982–) |
| radar | Sentinel-1 RTC (10 m, 2014–), ALOS PALSAR annual mosaic (25 m, 2015–2021) |
| vegetation | the optical sources, MODIS 16-day NDVI/EVI (250 m, 2000–) |
| elevation | Copernicus DEM GLO-30, NASADEM |
| water | JRC Global Surface Water occurrence/seasonality, OSM waterways |
| land cover / built-up | ESA WorldCover 2021 (10 m), IO annual LULC (10 m, 2017–2023), ESA CCI (300 m, 1992–2020) |
| forest | Hansen/UMD Global Forest Change 2000–2025 |
| temperature | MODIS 8-day LST (1 km) |
| fire | MODIS burned area (500 m, to July 2025) |
| precipitation | CHIRPS monthly (0.05°) |
| soil | ISRIC SoilGrids 2.0 (250 m) |
| population | WorldPop constrained 2020 (100 m) |
| boundaries | geoBoundaries ADM1/ADM2 |
| transport, buildings, facilities | OpenStreetMap via Overpass |
| events | GDACS |

`geofetch sources --check` probes every entry live. Adding a dataset is a YAML entry — see [CONTRIBUTING.md](CONTRIBUTING.md). Every entry is written from the provider's own documentation and verified by that check.

## Known limits

- Understanding is rule-based. An ask that matches no rule says so; `geofetch rules` shows the vocabulary.
- No soil-moisture source yet (the open ones need NASA Earthdata login).
- OpenStreetMap building queries are capped by area; Overpass servers can be slow or busy — `geofetch run` retries.
- WorldPop's server does not support partial reads, so the country file (~0.5 GB for India) is downloaded once per project.
- Planetary Computer's token service rate-limits anonymous use; geofetch backs off and retries. Set `PC_SDK_SUBSCRIPTION_KEY` for higher limits.
- "Expected clear coverage" is estimated from scene-level cloud percentages, not pixel masks.

## Licence and data attribution

Code: Apache-2.0 ([LICENSE](LICENSE)). Data keeps its own licence — each source's licence is recorded in the registry, in every catalogue item and in `REPORT.md`. Boundaries geocoded from place names are © OpenStreetMap contributors (ODbL).

Design notes and evidence: [docs/PRODUCT_DISCOVERY.md](docs/PRODUCT_DISCOVERY.md), [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md).
