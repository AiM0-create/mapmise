# Changelog

## 0.1.0a1 — first public alpha

- Redesigned interface following Apple's Human Interface principles: grouped lists, translucent toolbar and
  action bar, system typography, plain-language verdicts and warnings, light and dark appearance, and support for
  reduced motion, reduced transparency and increased contrast. New app icon.
- The application opens in its own window (pywebview: WebView2 on Windows, WKWebView on macOS, WebKitGTK on
  Linux), falling back to the browser where no system web view exists. `mapmise gui --browser` keeps the tab.
- Renamed from the working name "geofetch" (already used by a bioinformatics package) to **mapmise**.
- Desktop app for Windows, macOS (Apple silicon and Intel) and Linux: installers built and tested on each
  operating system by the release workflow; double-click to start, Quit button to stop, a second start reopens
  the running app. Includes `mapmise-cli` for the terminal.
- `mapmise selftest` checks an installation, including a real clipped download.
- Works on Windows and macOS: UTF-8 file handling everywhere, file names valid on every OS, per-OS data and
  cache folders, catalogue links always with "/", QGIS found in its standard install locations, mosaics (VRT)
  written without the gdalbuildvrt program (pixel-identical to it) and with relative paths.
- Personal library: every acquired file indexed across projects (SQLite, standard library). Reuse by local
  re-clip when it is exactly equivalent to a download (verified byte-identical); plans show "In your library";
  equally good scenes already held are preferred; `mapmise library` / Library screen; `--scan` for older projects.
- Recipes: `mapmise recipe export|run` — a project as one small file, rebuilt with checksum comparison.
- `mapmise refresh` extends the latest ask to today. Place boundaries are now fetched at full resolution so a
  taluk lies exactly inside its district.
- Built-in AI: a 23 MB sentence-embedding model (all-MiniLM-L6-v2, Apache-2.0) shipped in the package, offline,
  matches questions by meaning to ask rules (each rule now has example phrasings). Held-out evaluation:
  11/17 → 16/17 understood. Reasons shown in the plan; `--no-ai` to disable.
- Places that exist only as points (towns) resolve to the enclosing administrative area, stated in the plan.
- `mapmise gui`: a local app in the browser — ask, plan (untick, swap sources, details per row),
  fetching with progress, project view with Open in QGIS and the report. Standard library server bound
  to 127.0.0.1 with a per-session token; Leaflet (BSD-2) bundled for the map.

One prompt in; the right open geospatial data out — clipped, organised, dated and documented, locally.

- `mapmise ask "…"` understands the place (geocoded to a boundary with OpenStreetMap) and the period
  from plain English, creates a project, works out the data the analysis needs, plans it, shows why and
  how much, and fetches it after approval.
- 24 open sources behind 4 drivers (STAC incl. Planetary Computer signing, HTTP, OpenStreetMap Overpass,
  GDACS): Sentinel-1/2, Landsat 4–9, MODIS NDVI/LST/burned area, Copernicus DEM, NASADEM, ESA WorldCover,
  ESA CCI land cover, IO annual land cover, JRC surface water, ALOS PALSAR, Hansen forest change,
  SoilGrids, WorldPop, CHIRPS, geoBoundaries, OSM roads/buildings/waterways/facilities.
- 14 ask rules (flood, drought, vegetation, urban, water bodies, terrain, access, boundaries, rainfall,
  forest, fire, heat, soil, services) — composable, editable YAML.
- Planners by data shape: cloud-aware optical scene selection per tile with per-window feasibility
  verdicts; same-orbit radar pairs; ready-made product series; static layers; vector queries.
- Automatic fallback to cloud-independent data where optical is infeasible; automatic complement from a
  second source for years the first does not cover.
- Only the area of interest is transferred from cloud-optimised files; outputs are COGs in a common
  UTM projection (nearest-neighbour — no values are interpolated) and GeoPackages.
- Static STAC catalogue with SHA-256 per file and source URLs; `REPORT.md` acquisition record;
  `mapmise open` launches QGIS with per-period mosaics.
- `mapmise sources --check` probes every registry entry live.

Known limits: no soil-moisture source; OSM building
queries capped by area; some servers (WorldPop) do not support partial reads, so the country file is
downloaded once; no compositing or processing by design.
