# mapmise

**Say what you want to analyse and where. mapmise works out which open geospatial data that needs, checks whether it is actually usable for your area and period, fetches only your area, organises and dates it, and writes down exactly how — ready for QGIS or Python.**

```bash
mapmise ask "I heard a flood happened in Chitradurga in August 2026, help me analyse it locally"
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

> **Alpha.** Works end to end on real data today, with 24 open sources and a small built-in AI that runs on your computer. Expect rough edges — and please report them.

*The name:* chefs call laying out every ingredient, prepared and measured, before cooking *mise en place*. mapmise does that for maps: everything your analysis needs, fetched, clipped and labelled before you start.

## Why this exists

Every analysis starts with the same day of work: decide what data you need, find it across catalogues that all behave differently, discover that half of it is clouded out, fetch it, clip it, reproject it, name it sensibly, and try to remember how you got it. Experts do this on autopilot and still lose the day. Newcomers get it wrong. mapmise does that day for you — and keeps the record.

It is deliberately **not** an analysis tool. It never computes indices, masks clouds or classifies anything. If an open, analysis-ready product exists (radiometrically terrain-corrected radar, composited NDVI, land cover, surface water occurrence), you get that product.

## What makes it different

Plenty of excellent tools give you *access* to data: [EODAG](https://github.com/CS-SI/eodag), [earthlens](https://github.com/serapeum-org/earthlens), [pystac-client](https://github.com/stac-utils/pystac-client), the QGIS STAC browser, Copernicus Browser. You tell them which dataset; they fetch it. mapmise sits one step earlier and one step later:

- **From the analysis to the data.** "Flood" means radar before and after, a DEM, normal water extent, population, roads — each with a stated reason. Rules are plain YAML anyone can extend.
- **Feasibility before download.** Per tile and per month it picks the clearest scene and says when a period is *infeasible* for optical data — then adds a cloud-independent alternative automatically. When one product does not cover all your years, it stitches in another that does, and tells you which years nothing covers.
- **Only your area.** Cloud-optimised files are read in parallel windows; a 30 km area out of a 110 km Sentinel-2 tile transfers ~12× less.
- **The record is the output.** Every file lands in a static STAC catalogue with its SHA-256, source URL, query and the reason it was chosen, plus a human-readable `REPORT.md`.

## Install

### The app (Windows, macOS, Linux)

Download the file for your computer from the [latest release](https://github.com/AiM0-create/mapmise/releases/latest):

| Computer | File | Then |
|---|---|---|
| Windows 10/11 | `Mapmise-…-windows-x64-setup.exe` | run it; Mapmise appears in the Start menu. No administrator rights needed. |
| Mac with Apple silicon (M1 or newer) | `Mapmise-…-macos-arm64.dmg` | open it and drag Mapmise to Applications |
| Mac with Intel processor | `Mapmise-…-macos-x64.dmg` | same |
| Linux | `Mapmise-…-linux-x64.AppImage` | make it executable (`chmod +x`) and double-click it |

Starting Mapmise opens it in your web browser. It runs entirely on your computer; nothing is hosted anywhere.
Use the **Quit** button in the app to stop it.

**"Unknown publisher" warnings.** The alpha installers are not yet signed with a paid code-signing
certificate, so your computer will warn you the first time:

- **Windows** (SmartScreen "Windows protected your PC"): click **More info → Run anyway**.
- **macOS** ("Apple could not verify…"): click **Done**, then open **System Settings → Privacy & Security**, scroll
  down and click **Open Anyway** next to Mapmise. You only need to do this once.

Each release lists SHA-256 checksums of every file (`SHA256SUMS.txt`) and is built from this repository by
[a public workflow](.github/workflows/release.yml), which tests every build on its own operating system first.

To check an installation: Windows `"%LOCALAPPDATA%\Programs\Mapmise\mapmise-cli.exe" selftest`,
macOS `/Applications/Mapmise.app/Contents/MacOS/mapmise-cli selftest`, Linux `./Mapmise-…AppImage cli selftest`.

### With Python (for the command line and scripting)

Python ≥ 3.12:

```bash
pip install mapmise-0.1.0a1-py3-none-any.whl    # from the release page
mapmise selftest
mapmise gui
```

Or from source:

```bash
git clone https://github.com/AiM0-create/mapmise.git && cd mapmise
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/mapmise --version
```

GDAL comes bundled with the rasterio wheel; no separate install is needed. QGIS is optional (for opening projects
in QGIS); mapmise finds it on the PATH, in the standard Windows install folders, or in macOS Applications.

## The app

```bash
mapmise gui
```

opens mapmise in your browser. It runs entirely on your computer (nothing is hosted anywhere) and has four screens:

1. **Ask** — type what you want to analyse; optional place, dates, event date or your own boundary file.
2. **Plan** — what it understood, the area on a map, and every dataset with its verdict, size and reasons. Untick what you don't want, swap a source from the dropdown, click a row for the details, then **Fetch**. Files you already have in the project are marked and not counted.
3. **Fetching** — progress per source; safe to close, it resumes.
4. **Project** — what you have, what's missing and why, the report, **Open in QGIS**, or ask something else about the same area.

New projects go to `~/mapmise-projects` (change with `--workspace`). Everything the app does is written to the same project files as the command line.

## Built-in AI

mapmise ships a small language model (23 MB, runs offline on your CPU, no account or API key). It lets you ask in your own words:

> “Farmers near Hiriyur lost their groundnut because it barely rained in July 2026”

has no word like *drought* in it, yet the plan says:

```
drought   (meaning: close to “farmers lost their harvest because it did not rain” (0.62))
rainfall  (keyword)
```

The model only chooses among the ask rules that already exist, and always says why. It cannot invent a dataset, a scene, a date or a place; everything after understanding is the same deterministic engine. On a held-out set of realistic questions it raised understanding from 11/17 (keywords alone) to 16/17 — see `docs/EXPERIMENTS.md` (E5). Turn it off with `--no-ai`.

Model: sentence-transformers/all-MiniLM-L6-v2 (Apache-2.0), 8-bit ONNX, run with onnxruntime.

## Your library

Every file mapmise downloads is indexed in one personal library (a small database file in your home folder). Three things follow:

- **Reuse instead of download.** Ask about Hiriyur taluk after you already fetched Chitradurga district, and the plan says *In your library* — the files are clipped from what you have, in seconds, with 0 MB transferred. mapmise reuses a file only when that gives exactly the same pixels as a fresh download (same scene, same band, still on the source's own grid, same projection, fully covering the new area); otherwise it downloads. When two scenes are equally good, it prefers the one you already have; it never picks a worse scene for that.
- **What do I already have here?** `mapmise library --place "Hiriyur"` or the Library screen in the app.
- **Share a recipe, not gigabytes.** `mapmise recipe export <project>` writes one small file (area, questions, the exact scenes and URLs chosen, checksums). `mapmise recipe run recipe.json <folder>` rebuilds the dataset and reports which files are byte-identical. Live sources such as OpenStreetMap are reported as changed by design.

`mapmise refresh <project>` extends the latest question to today and fetches only what is new. Index projects made before the library existed with `mapmise library --scan ~/mapmise-projects`.

## Use from the command line

```bash
mapmise ask "Assess agricultural drought in Chitradurga during the 2026 monsoon"
mapmise ask "Urban expansion of Bengaluru between 2005 and 2025" --dry-run
mapmise ask "Forest loss in Wayanad since 2015"
mapmise ask "Hospital and school access in Tumakuru district"
mapmise ask "Urban heat in Bengaluru in April 2026"
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
mapmise status  ./chitradurga-2026-08    # what the project holds, what is missing and why
mapmise run     ./chitradurga-2026-08    # resume or retry — completed files are skipped
mapmise open    ./chitradurga-2026-08    # per-period mosaics, vectors and the AOI in QGIS
mapmise report  ./chitradurga-2026-08    # regenerate REPORT.md
mapmise sources [--theme water]          # the data registry
mapmise rules                            # the words mapmise understands
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
  data/vrt/           per-period mosaics built by `mapmise open`
  REPORT.md           the acquisition record in plain language
  .cache/             catalogue searches and file sizes; safe to delete

Whole files that are identical across projects (a country population raster, a monthly global rainfall file) are cached once in `~/.cache/mapmise/files` (override with `MAPMISE_CACHE`).
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

`mapmise sources --check` probes every entry live. Adding a dataset is a YAML entry — see [CONTRIBUTING.md](CONTRIBUTING.md). Every entry is written from the provider's own documentation and verified by that check.

## Known limits

- Understanding is rule-based. An ask that matches no rule says so; `mapmise rules` shows the vocabulary.
- No soil-moisture source yet (the open ones need NASA Earthdata login).
- OpenStreetMap building queries are capped by area; Overpass servers can be slow or busy — `mapmise run` retries.
- WorldPop's server does not support partial reads, so the country file (~0.5 GB for India) is downloaded once per project.
- Planetary Computer's token service rate-limits anonymous use; mapmise backs off and retries. Set `PC_SDK_SUBSCRIPTION_KEY` for higher limits.
- "Expected clear coverage" is estimated from scene-level cloud percentages, not pixel masks.

## Licence and data attribution

Bundled third-party components (the AI model, Leaflet) are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).


Code: Apache-2.0 ([LICENSE](LICENSE)). Data keeps its own licence — each source's licence is recorded in the registry, in every catalogue item and in `REPORT.md`. Boundaries geocoded from place names are © OpenStreetMap contributors (ODbL).

Design notes and evidence: [docs/PRODUCT_DISCOVERY.md](docs/PRODUCT_DISCOVERY.md), [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md).
