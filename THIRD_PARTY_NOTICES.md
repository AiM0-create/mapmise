# Third-party components bundled with mapmise

mapmise itself is licensed under Apache-2.0 (see `LICENSE`). The following components are included in the
package with their own licences:

| Component | Where | Licence |
|---|---|---|
| sentence-transformers/all-MiniLM-L6-v2, 8-bit ONNX export (model and tokenizer) | `mapmise/ai/model/` | Apache-2.0 (`mapmise/ai/model/LICENSE`) |
| Leaflet 1.9.4 | `mapmise/gui/static/vendor/leaflet/` | BSD-2-Clause (`mapmise/gui/static/vendor/leaflet/LICENSE`) |

With the Python package, dependencies are installed separately and keep their own licences.

## Desktop app (installers)

The Windows, macOS and Linux apps additionally bundle, unmodified, the following components with their
licences (the licence files are inside the app folder, next to each component):

| Component | Licence |
|---|---|
| Python 3.12 | PSF-2.0 |
| GDAL, PROJ (via rasterio, pyogrio, pyproj wheels) | MIT |
| GEOS (via Shapely) | LGPL-2.1 — dynamically linked, replaceable; source at https://github.com/libgeos/geos |
| libcurl, libtiff, libpng, libjpeg-turbo, zlib, SQLite, OpenSSL and other libraries inside the rasterio/pyogrio wheels | permissive (curl, libtiff, zlib, IJG/BSD, public domain, Apache-2.0) |
| rasterio, Shapely, numpy, pandas, GeoPandas, httpx, httpcore, pystac, pystac-client | BSD-3-Clause |
| pyogrio, pyproj, PyYAML, onnxruntime | MIT |
| tokenizers | Apache-2.0 |
| certifi (certificate bundle) | MPL-2.0 — unmodified; source at https://github.com/certifi/python-certifi |
| PyInstaller bootloader | GPL-2.0 with an exception that allows distributing bundled applications under any licence |

## Data

mapmise downloads data; it does not redistribute any. Every dataset keeps its provider's licence, recorded in
`mapmise/registry/sources.yaml`, in each project's STAC catalogue and in its `REPORT.md`. Map tiles in the app
and boundaries geocoded from place names are © OpenStreetMap contributors (ODbL).
