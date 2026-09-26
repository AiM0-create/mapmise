# Changelog

## 0.1.0a1 — first public alpha

One prompt in; the right open geospatial data out — clipped, organised, dated and documented, locally.

- `geofetch ask "…"` understands the place (geocoded to a boundary with OpenStreetMap) and the period
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
  `geofetch open` launches QGIS with per-period mosaics.
- `geofetch sources --check` probes every registry entry live.

Known limits: keyword understanding (no language model yet); no soil-moisture source; OSM building
queries capped by area; some servers (WorldPop) do not support partial reads, so the country file is
downloaded once; no compositing or processing by design.
