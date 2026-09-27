# Third-party components bundled with geofetch

geofetch itself is licensed under Apache-2.0 (see `LICENSE`). The following components are included in the
package with their own licences:

| Component | Where | Licence |
|---|---|---|
| sentence-transformers/all-MiniLM-L6-v2, 8-bit ONNX export (model and tokenizer) | `geofetch/ai/model/` | Apache-2.0 (`geofetch/ai/model/LICENSE`) |
| Leaflet 1.9.4 | `geofetch/gui/static/vendor/leaflet/` | BSD-2-Clause (`geofetch/gui/static/vendor/leaflet/LICENSE`) |

Python dependencies (installed separately, not bundled) keep their own licences: GDAL/rasterio, pystac,
pystac-client, Shapely, GeoPandas, pyproj, numpy, httpx, PyYAML, onnxruntime, tokenizers.

## Data

geofetch downloads data; it does not redistribute any. Every dataset keeps its provider's licence, recorded in
`geofetch/registry/sources.yaml`, in each project's STAC catalogue and in its `REPORT.md`. Map tiles in the app
and boundaries geocoded from place names are © OpenStreetMap contributors (ODbL).
