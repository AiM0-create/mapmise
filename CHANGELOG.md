# Changelog

## 0.1.0a5

- Linux: starting the AppImage from the applications menu or a double-click crashed in 0.1.0a4 when the system has
  no web view for the app's own window. Mapmise now checks for one first and otherwise opens in the browser, and
  falls back to the browser if a window cannot open for any other reason. The Linux release build now tests this.

## 0.1.0a4

From the first real-world test on Windows:

- The app window opens immediately with a start screen while the data engine loads behind it; the first start on
  Windows can take up to a minute while the system checks the new app, and the screen now says so.
- Only one copy runs: a second start — even while the first is still loading — brings the existing window forward.
- Planning shows each step as it happens (finding the place, checking each dataset) with elapsed time and Cancel.
- Large areas: above 50,000 km² planning must be confirmed; above 100,000 km² scene-by-scene imagery is not planned
  and area-wide products are chosen instead (for example MODIS vegetation for a whole country). Catalogue searches
  are capped, so no plan can page through a catalogue for hours. Previously "forest loss in Brazil" planned for hours
  without any sign of progress; it now answers in seconds.

## 0.1.0a3

- NASA Earthdata support: the user's own access token (never a password), stored privately on their computer and
  sent only to NASA hosts; signed storage links are used in memory and never recorded. Settings → NASA Earthdata in
  the application, and `mapmise earthdata login | status | check | logout`.
- New sources: HLS Landsat and Sentinel-2 surface reflectance (30 m) and OPERA DSWx-HLS surface-water maps.
  New `surface_water` theme; flood and water-body questions now ask for ready-made water maps.
- Without a token, plans state which requirements an Earthdata dataset would have met.

## 0.1.0a2

- New Mapmise logo: a folded map in the shape of an M, with contour lines. Used for the app icon on Windows,
  macOS and Linux, in the application and in the documentation.

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
