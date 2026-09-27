# Product Discovery: Project-Aware Geospatial Data Acquisition

Status: discovery complete, **qualified GO** (see §21). E1 and E2 executed 2026-09-15. Positioning revised 2026-09-18 (§9a) and again 2026-09-26 after finding earthlens (§9b). **Alpha 0.1.0a1** implements §9b — see `README.md` and `docs/EXPERIMENTS.md`.
Date: 2026-09-15. Environment inspected: empty repo; host has Python 3.14, GDAL 3.12.2, QGIS 4.2.2, Node 22; no aria2/rclone/EODAG installed.

Evidence for claims below comes from (a) live probes against provider APIs run today (scripts in `docs/probes/`, results quoted inline), (b) PyPI metadata, (c) primary docs and repos linked in §22. Where something is an inference rather than a measurement it is marked *(inference)*.

---

## 1. Problem definition

A practitioner (researcher, agency analyst, student, consultant) knows the **analysis** they want to do — "assess agricultural drought over Karnataka in the 2026 monsoon" — and must turn that into a **correctly selected, locally prepared, reproducible dataset**. Today that bridge is built by hand, every time:

1. decide which products/bands/periods/filters are appropriate (domain knowledge, easy to get subtly wrong);
2. find the assets in one or more catalogues, each with different auth, asset naming, and download paths;
3. check coverage across the AOI and time (not just "scenes matched");
4. download only what is needed, reliably, through expiring/signed/authenticated URLs and quotas;
5. clip/reproject/organise so QGIS/Python can consume it;
6. remember what was done, so it can be re-run, audited, or extended ("finish the August series").

Steps 1, 3, and 6 are where existing tools are weakest. Steps 2, 4, 5 are largely solved by libraries and must be reused, not rebuilt.

Concrete evidence that step 3 is a real problem (probe, Earth Search, Karnataka bbox, Jul–Aug 2026):

| Query | Matched scenes |
|---|---|
| Sentinel-2 L2A, all cloud | 1,349 |
| Sentinel-2 L2A, `eo:cloud_cover < 20` | **35**, spanning 17 MGRS tiles (Karnataka needs ~30) |

A "cloud < 20 %" filter — the default in every browser — silently yields a dataset with holes in it during monsoon. The right plan is per-tile, per-month best-available with an explicit coverage report. No current GUI tool computes that.

## 2. Existing ecosystem (what exists, what it does)

### Discovery / search
| Tool | What it solves | What it doesn't | Activity |
|---|---|---|---|
| **STAC + pystac-client** (0.9.0, Jul 2025) | Standard search across Earth Search, CDSE, MPC, USGS LandsatLook, many more. Probes today: all three big catalogues answered a Karnataka bbox query in 1–3 s anonymously. | Asset naming is not standardised (`red`/`nir` vs `B04`/`B08` vs `B04_10m`); `file:size` present only on CDSE; only Earth Search returns a match count; no auth for assets. | Core of the ecosystem, very active. |
| **EODAG** (4.8.0, released 2026-09-11, OSGeo project, Apache-2) | Unified search+auth+download over ~30 providers (CDSE, CDSE-S3, Earth Search, MPC, USGS, Creodias, Geodes, DEDL, EUMETSAT…). Plugin architecture (search / download / auth). Per-asset regex filtering, parallel downloads, download-record dedup, offline-product ordering with wait/retry, progress callbacks. | No Indian providers. Probe: with `cloudCover=20` on `earth_search` every filter syntax returned 0 matches while the raw STAC query returned 35 — cause not investigated, but it shows the abstraction leaks and server-side filters cannot be trusted blindly. Product-type naming differs from STAC collection IDs (`S2_MSI_L2A_COG` vs `sentinel-2-l2a`). No coverage planning, no project state, no provenance beyond a hash record. | Very active (monthly releases). |
| **Copernicus Browser / CDSE** | Excellent visual browsing, OData, STAC, S3, openEO. | Web only; per-account quota (12 TB/month S3 free tier, bandwidth throttling after quota); no local project awareness. | Active. |
| **Earthdata Search / earthaccess** (0.19.0, Sep 2026) | NASA CMR discovery + Earthdata Login auth + download. | NASA only. | Active. |
| **ASF Vertex / asf_search** (13.1.1, Sep 2026) | Best Sentinel-1 search (baseline, orbit, stack tools), HyP3 RTC on-demand. | S1 only; Earthdata login. | Active. |
| **QGIS native STAC** (3.40+, filtering in 3.42+) | Browse/filter STAC in Data Source Manager, load COGs directly. | "Download Assets" is documented as broken; no planning, no clipping, no manifest. | Core QGIS. |
| **QGIS STAC API Browser plugin** (stac-utils, 1.1.2, Apr 2024) | Search + per-item asset download. | Stale (2024), Qt5 era; QGIS 4 (Mar 2026) broke Qt5 plugins. | Low. |
| **Open Geodata Browser** (QGIS plugin 1.2.4, Aug 2026) | Multi-STAC search, batch download, auto-load. Closest "download GUI" in QGIS. | No objective → requirement step, no coverage analysis, no project state, no provenance; needs manual pip install of `open-geodata-api`. | Active, single maintainer. |
| **STAC Browser** (Radiant Earth) | Web catalogue viewer. | No download, no project. | Active. |

### Transfer
| Tool | Relevance |
|---|---|
| **stac-asset** (0.4.7, Jun 2025, stac-utils) | Async download of STAC items/assets with pluggable clients: `HttpClient`, `S3Client` (incl. requester-pays), `PlanetaryComputerClient` (auto SAS signing), `EarthdataClient`, `FilesystemClient`. This is exactly the auth-aware transfer abstraction we need. |
| **GDAL `/vsicurl/` + `/vsis3/` + `/vsiaz/`** | For COG assets, `gdalwarp -cutline AOI` reads **only the AOI window** over HTTP range requests. Probe: Earth Search B04 COG is 227 MB with `Accept-Ranges: bytes`; a district-sized AOI needs a fraction of that. This alone makes a segmented downloader irrelevant for COG providers. |
| **aria2** | Upstream last release 1.37.0 (2023), effectively unmaintained. `aria2-next` is a maintained drop-in fork (2026). Neither can re-sign expiring MPC SAS URLs (~24 h expiry, 409 when unsigned — probed), refresh CDSE OAuth tokens, or do requester-pays S3. |
| **rclone** | Excellent for S3/Azure bulk sync (CDSE `eodata`, MPC blobs) but a separate binary, no per-asset planning. Optional later for whole-archive mirroring. |
| **EODAG download plugins** | HTTP/S3/AWS, per-asset, parallel; extraction. Adequate for whole-product downloads from CDSE. |
| **IDM / FDM / JDownloader / Motrix / Motrix Next / Persepolis / AriaNg** | Lessons only: segmented range downloads, resume from `.aria2` control files, RPC daemon lifecycle pain (port conflicts, orphan processes — Motrix Next's changelog is full of these), plugin decay (JDownloader). They solve URL lists, not datasets. |

### Processing
GDAL/OGR 3.12 (already installed), Rasterio 1.5.1, GeoPandas 1.1.4, Shapely, PROJ, odc-stac 0.5.3 / stackstac / cubo (lazy STAC → xarray, no download). All mature. Nothing to build here beyond thin orchestration.

### Cloud-first platforms (the biggest strategic threat)
- **openEO on CDSE**: 10,000 free credits/month, ~25 % cheaper billing since Mar 2026; process-then-download-results model.
- **Google Earth Engine**: dominant for large-area analysis; not local, licensing limits for commercial use.
- **EOPF Sentinel Zarr**: ESA is migrating Sentinel from SAFE to cloud-native Zarr ("data as an API instead of a file"); explorer launched Feb 2026. Long-term this reduces the need to download full products — but it *increases* the need to fetch AOI windows efficiently, which is our transfer model anyway.
- **Planetary Computer**: catalogue and STAC API remain free (2026); Pro is the paid private-catalogue product. `sentinel-1-rtc` is now global — probe found 2026 RTC items over Karnataka — so analysis-ready S1 exists without SNAP.

### AI / agent layer
| Project | What it is | Distance from our workflow |
|---|---|---|
| **Earth Copilot / Planetary Explorer** (Microsoft+NASA, MIT) | Multi-agent Azure app; NL → STAC/MPC → cloud visualisation; MCP tools for VS Code/Claude Desktop. | Cloud-only, Azure subscription, no local acquisition, no project manifest. |
| **LLM-Find / AutonomousGIS GeodataRetrieverAgent** (Ning, Li et al., 2025; QGIS plugin) | LLM picks a data source from "handbooks", generates and debugs Python to fetch data. 80–90 % success on vector/tabular sources (OSM, Census, ESRI basemap). | Code-generation approach (the opposite of our deterministic principle); no EO planning, coverage, or provenance. |
| **Risk-Aware LLM Agents for Geospatial Data Retrieval** (arXiv 2606.15077, Jun 2026) | NL → API calls with NeMo guardrails; adversarial evaluation shows prompt-level guardrails are insufficient, intercept-level ones needed. | Validates our "LLM proposes, deterministic layer validates, human approves" split. Not open source. |
| **chuk-mcp-stac** (IBM, 0.3, Mar 2026, Apache-2) | MCP server: search Earth Search/MPC/LandsatLook, download bands/RGB/composites with bbox clipping, temporal composites. "Demonstration, as-is." | Technically the closest thing to our acquisition+prep loop, driven from an LLM client. No persistent project, no coverage planning, no provenance, in-memory only. |
| **geo-mcp-servers** (sparkgeo list, 57 servers, Sep 2026) | 15 STAC/EO MCP servers (stac-mcp, planetary-computer-mcp, copernicus-mcp, earthdata-mcp, Tilebox…). | Discovery/analysis oriented; Tilebox is commercial dataset/workflow SaaS. |
| **Kue (Bunting Labs)**, **IntelliGeo**, **GIS Copilot / SpatialAnalysisAgent** | QGIS in-app assistants for styling/geoprocessing via PyQGIS generation. | Analysis, not acquisition. Kue is $19/mo SaaS. |
| **OpenEarthAgent**, **GeoGPT**, **Geo-OLM** | Research agent frameworks / models. | Not products; no local data management. |
| **Planet agentic AI, SkyFi MCP** | Commercial tasking/ordering. | Different market. |

## 3. Competitor matrix (against the proposed workflow)

Legend: ● full, ◐ partial, ○ none.

| Capability | EODAG | Open Geodata Browser | QGIS STAC | Copernicus Browser | Earth Copilot | chuk-mcp-stac | LLM-Find | openEO/GEE | **Proposed** |
|---|---|---|---|---|---|---|---|---|---|
| Persistent project context (AOI, objective, period) | ○ | ○ | ◐ (QGIS project) | ○ | ◐ (chat history) | ○ | ○ | ◐ (scripts) | ● |
| Objective → data requirements (explained) | ○ | ○ | ○ | ○ | ◐ | ○ | ◐ | ○ | ● |
| Real catalogue discovery | ● | ● | ● | ● | ● | ● | ◐ | ● | ● (reuse) |
| Coverage-aware scene selection (per tile/period) | ○ | ○ | ○ | ○ | ○ | ○ | ○ | n/a | ● |
| Size/quota estimate before download | ◐ | ○ | ○ | ◐ | ○ | ○ | ○ | ● (credits) | ● |
| Human-approved plan | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ● |
| Auth-aware, resumable local acquisition | ● | ◐ | ○ (broken) | ◐ | ○ | ◐ | ◐ | n/a | ● (reuse) |
| Band-/AOI-window-level transfer (COG) | ◐ | ◐ | ● (stream) | ○ | n/a | ● | ○ | n/a | ● |
| Local project state / gap analysis | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ● |
| Post-download prep (clip, COG, organise) | ○ | ◐ | ○ | ○ | ○ | ◐ | ○ | ● (cloud) | ● |
| Provenance / re-runnable recipe | ◐ (hash record) | ○ | ○ | ○ | ○ | ○ | ○ | ◐ | ● |
| Works offline / without LLM | ● | ● | ● | ○ | ○ | ○ | ○ | ○ | ● |
| Maintained 2026 | ● | ● | ● | ● | ◐ | ◐ | ◐ | ● | — |

Added 2026-09-26: **earthlens** — persistent project ○, objective→requirements ○ (variable lookup only), real discovery ●, coverage-aware selection ○, size estimate ◐ (dry-run validation), approval ○, local acquisition ●, window-level transfer ◐ (bbox mosaics), gap analysis ○, prep ◐ (clip, GeoTIFF), provenance ○, offline ●, maintained ● (61 providers). See §9b.

No existing tool covers the left column's first two rows plus coverage planning, gap analysis, and provenance together. **No mature product makes this project redundant.** The closest *technical* neighbour is `chuk-mcp-stac` (demo quality, stateless); the closest *product* neighbours are Open Geodata Browser (QGIS, no planning/state) and EODAG (library, no planning/state).

## 4. User pain points (from docs, forums, and the probes)

1. **Coverage blindness** — "35 scenes matched" hides that half the AOI has no clear observation (§1).
2. **Asset heterogeneity** — same band, three names and three access schemes (probed: `red` HTTPS COG; `B04` SAS-signed; `B04_10m` JP2 on `s3://eodata`).
3. **Expiring/authenticated URLs** — MPC SAS tokens ~24 h; CDSE OAuth tokens; requester-pays S3 for S1 on AWS. Generic downloaders cannot resume across these.
4. **Quotas** — CDSE throttles after monthly volume; users discover this mid-download.
5. **Over-download** — whole SAFE zips (~1 GB S2, ~1.5 GB S1 GRD) when two 10 m bands over a district are needed.
6. **"What did I download and why?"** — folders of `.SAFE` with no query record; reproducibility surveys report >40 % of Earth-science researchers cannot reproduce their own work.
7. **Tool fragmentation** — one script per provider, re-written per project; QGIS 4's Qt6 break (Mar 2026) has orphaned many acquisition plugins.
8. **Sentinel-1 correctness** — pre/post pairs with mismatched relative orbit or orbit direction are a classic beginner error; nothing in the GUI tools prevents it.

## 5. Problems already solved (reuse, do not rebuild)

- Catalogue search: STAC / pystac-client; EODAG for non-STAC providers (OData, USGS M2M, EUMETSAT).
- Provider auth + asset transfer: stac-asset clients; EODAG auth plugins; GDAL virtual filesystems.
- Windowed access to COGs: GDAL `/vsicurl/`.
- Clip / reproject / mosaic / VRT / COG / resample: GDAL, Rasterio.
- Geometry: Shapely, GeoPandas, PROJ.
- Lazy analysis on remote data: odc-stac, stackstac (for users who don't need local files).
- S1 analysis-ready data: MPC `sentinel-1-rtc` (global), ASF HyP3 (on-demand).
- Local catalogue format QGIS already understands: static STAC (QGIS ≥ 3.40 browses it natively).

## 6. Problems still poorly solved

1. Objective → explicit, explained data requirements.
2. Coverage-aware, quota-aware acquisition **planning** shown before execution.
3. A durable, local **project manifest** with gap analysis ("what's missing for August?").
4. Provenance that captures query + selection + transfer + processing as a re-runnable recipe.
5. One transfer layer that handles COG windows, signed URLs, S3 credentials, resume, and verification uniformly.
6. Sentinel-1 pair/stack selection rules (orbit, direction, temporal baseline) exposed to non-experts.

All six are **deterministic** problems. The LLM is useful only for #1's natural-language entry point and for explaining plans.

## 7. Exact gap

**A local, provider-agnostic acquisition planner + project manifest.** Given `(objective, AOI, period, existing local state)`, produce an inspectable plan `(collection, filters, per-tile/per-window scene selection, assets, bytes, quota impact, preparation steps, explanations)`; execute it through reused transfer/processing libraries; record everything as a static STAC catalogue + recipe that QGIS and Python can read directly.

## 8. Why existing tools don't fill it

- EODAG/pystac-client are libraries with no notion of a project, coverage, or plan; they answer "what matches?" not "what should I get?".
- Browser GUIs (Copernicus, QGIS, Open Geodata Browser) stop at "download these items".
- Cloud platforms (openEO, GEE, Earth Copilot) deliberately avoid local files.
- MCP servers are stateless tool surfaces for chat clients; provenance and gap analysis are not their concern.
- Agent research (LLM-Find, GIS Copilot) generates code — inherently non-reproducible and not what a practitioner wants to audit.

## 9. Proposed product wedge

Positioning (answering the five options): **(4) narrowed** — *"a project-first, reproducible EO data acquisition & preparation tool"*. Not "download manager" (1, 2: solved and commoditised), not "agent" (4 as literally worded: the LLM is optional). Option 3's "context-aware client" is close, but the wedge is **planning + manifest + provenance**, with natural language as one input method.

Wedge statement: *You describe the analysis and the area; it tells you exactly which scenes/bands you need and why, what's already on disk, how big the rest is, then fetches and prepares only that — and writes down how, so you or a reviewer can re-run it.*

First user: Indian/regional agri-drought and flood researchers using QGIS + Python on laptops with metered connectivity (the founder's context; a real, underserved segment where cloud-first is not the default).

### 9a. Revised positioning (2026-09-18)

The owner's intent is broader than §9: not an EO acquisition tool but **a local data-sourcing engine for open geospatial data** — *"I want to do X analysis of Y area"* → the engine works out what data that needs (raster and vector, imagery and layers), finds it across the open community's catalogues and APIs, fetches only the area, organises and dates it, and documents it. No processing; analysis-ready open products only. The convenience of the boring pre-analysis day is the product, for experts and non-experts alike.

Consequences, and what changed in the design:
- **No workflow code.** Domain knowledge lives in two editable data files: a *source registry* (datasets described by what they are — theme, shape, resolution, licence, access) and *ask rules* (words → generic needs). A "flood" is a rule, not a module. Five different asks (flood, drought, urban, reservoir, road access) resolve through the same code with zero per-ask logic.
- **Few drivers, many sources.** Four access drivers (STAC, HTTP, Overpass, GDACS) cover the alpha's 13 sources and, by construction, most of the open ecosystem. Breadth is added by editing YAML.
- **Planners by data shape**, not by analysis: dated scenes (optical per tile / radar per orbit), static layers, whole-file series, vector queries.
- The §17 MVP (one sensor) was the right first step because scene planning is the hardest shape; it is now one planner among four.
- The moat is the curated registry plus the community that grows it — the thing a generic AI cannot invent correctly.

### 9b. earthlens, and the final wedge (2026-09-26)

[earthlens](https://github.com/serapeum-org/earthlens) (serapeum-org, GPL-3.0, first release May 2026, 0.25.0 on 2026-09-23, 21 releases) is a unified download facade over **61 providers** — imagery (GEE, STAC, Sentinel Hub, openEO, Earthdata, ASF), climate (ERA5, CMIP6), hazards (GDACS, FIRMS), population, soils, OSM, geoBoundaries and more — with `find("precipitation")` variable lookup across providers, dry-run validation, clipping to a bbox, analysis-ready GeoTIFFs and idempotent re-runs.

What it solves: breadth of *access*, better than we can match. What it does not do (from its docs and code examples): no analysis → data reasoning (`find` is a variable-name lookup), no feasibility or coverage verdicts (its STAC backend mosaics every match), no project state or gap reporting, no provenance record or report, no multi-source approval step.

Consequences, decided with the owner:
- **Do not race on breadth.** Grow the registry by what asks need, not towards a provider count.
- **Do not depend on earthlens.** Its GPL-3.0 licence would bind this project; the owner chose to stay Apache-2.0. Sources are re-engineered clean-room: each entry is written from the *provider's* documentation and verified by our own live probe (`geofetch sources --check`); no code or catalogue files are copied from earthlens or any other tool.
- **The wedge is the layer nobody has:** ask → needs with reasons → feasibility per window → automatic fallback and stitching → approval → area-only fetch → catalogue + report. earthlens makes access easier every month; that makes this layer more valuable, not less.
- THOR (FM4CS; a Sentinel-1/2/3 foundation model, MIT) is downstream: a consumer of prepared local stacks, not a competitor.

## 10. Proposed architecture

Single-process local application. Python core (it is where GDAL/STAC/EODAG live), thin UI on top. No microservices.

```
┌──────────────── UI (CLI first; desktop shell later) ────────────────┐
│ Projects · Map/AOI · Data (state) · Plans · Activity                  │
└───────────────┬───────────────────────────────────────────────────────┘
                │ typed commands / JSON
┌───────────────▼──────────────── core (Python package) ───────────────┐
│ ProjectStore   ← SQLite index + static STAC catalogue on disk         │
│ IntentInterpreter (LLM, optional) → StructuredIntent (pydantic)       │
│ Planner (deterministic): requirements → discovery → selection →       │
│         size/quota → prep steps → AcquisitionPlan (JSON, editable)    │
│ Validator: schema + geometry + provider capability + quota rules      │
│ DiscoveryAdapters: STACAdapter(pystac-client) · EODAGAdapter          │
│ TransferEngine: GDALWindowed · HTTPRange · StacAssetClients           │
│ Preparer: GDAL/Rasterio ops (clip/reproject/COG/VRT/organise)         │
│ Provenance: recipe.json + STAC items + processing log                 │
└───────────────────────────────────────────────────────────────────────┘
```

Every arrow between boxes is a plain data object (pydantic model, serialisable to JSON). The plan is the contract: UI edits it, validator checks it, executor runs it, provenance stores it.

## 11. Deterministic vs LLM responsibilities

| Deterministic (code) | LLM (optional, replaceable) |
|---|---|
| Objective template → requirement set (rule table, e.g. `drought.agri → [optical_vegetation, rainfall, soil_moisture]`) | Map free text to an objective template + parameters; explain choices in prose |
| AOI parsing, buffering, CRS, MGRS/tile intersection, coverage fraction | Resolve place names to a *suggestion* the user confirms (geocoding is still deterministic) |
| Catalogue queries, pagination, counts, per-item metadata | — |
| Scene ranking: cloud, coverage, temporal spacing, orbit consistency | — |
| Byte estimates (STAC `file:size`, HEAD `Content-Length`, or window-size formula for COG reads) | — |
| Quota checks against provider rules | — |
| Checksums (provider-supplied where available; local SHA-256 always) | — |
| Gap analysis vs manifest | Phrase the gap report; suggest which gap to fill first |
| All processing | — |

Hard rule enforced by types: `StructuredIntent` contains **no** scene IDs, URLs, sizes, coordinates, or CRS fields. Those exist only in `AcquisitionPlan`, which only the Planner may construct. The Risk-Aware Agents paper's finding (prompt-level guardrails are insufficient; intercept at the API boundary) supports doing this at the type boundary rather than in prompts.

Local models: Ollama-class models can fill `StructuredIntent` via constrained JSON output; not an MVP requirement.

**Decision 2026-09-27 (owner):** the AI must be shipped inside the software — no API, no account, offline. Implemented as a 23 MB sentence-embedding model that maps asks to existing rules by meaning (E5). A small generative model remains a later option for multi-part questions, under the same rule: its output is a structured request the deterministic engine validates.

## 12. Discovery strategy

- **Primary: STAC via pystac-client** for Earth Search (S2 L2A COG, anonymous), Planetary Computer (S2 L2A, S1 GRD, S1 RTC; free with signing), CDSE STAC (S2, S1 COG; auth for assets). Probed today; all responsive.
- **Own a thin normalisation layer**: canonical band names (`red`, `nir`, `swir16`, `vv`, `vh`…) mapped per collection; canonical fields (`cloud_cover`, `relative_orbit`, `orbit_state`, `tile`, `epsg`). This is small (a YAML per collection) and unavoidable — EODAG has its own such mapping but keyed to OpenSearch names.
- **Filter client-side** for anything beyond bbox/datetime/collection; fetch metadata pages and apply filters deterministically. Reason: server-side filter semantics vary and, per the probe, EODAG's cloud filter on Earth Search returned nothing.
- **EODAG as the second adapter**, used for providers with no usable STAC (USGS M2M, EUMETSAT, Geodes, OData-only paths) and for its ordering/wait logic. Do not force all providers through it; do not force STAC providers around it either — the `DiscoveryAdapter` interface (`search(intent) -> [NormalisedItem]`) hides the choice.
- Bhoonidhi: API exists but is by-request (email NRSC); `bhoonidhi-downloader` (PyPI) is a community SDK. Post-MVP adapter candidate; important for the target user.
- **Deferred**: USGS, NASA Earthdata (earthaccess for SMAP/soil moisture later), ASF (asf_search for S1 stacks later), AWS requester-pays collections.

## 13. Transfer-engine recommendation

**Do not build the product around aria2. Do not build a downloader.** Evidence:

1. The dominant case (S2 bands over an AOI from a COG host) is best served by **GDAL windowed reads** over `/vsicurl/`, which download only the needed blocks of the COG. Measured (E2, `docs/EXPERIMENTS.md`): pixel-identical output; bytes scale with AOI∕tile fraction (1.6× less for a district that fills a tile, 12.6× for a 30 km box, 77× for a 10 km box). Caveat: sequential range fetching is latency-bound and can be slower than one full stream; with 8–16 parallel block reads the windowed path is faster at every AOI size. So the engine is *parallel block reads → local COG*, not a single `gdalwarp`. No generic downloader can do this.
2. Signed URLs (MPC, ~24 h), OAuth (CDSE), requester-pays S3 (Earth Search S1): the engine must know how to **re-authorise mid-job**. `stac-asset` already has clients for exactly these; aria2 can't.
3. Whole-file transfers (SAFE zips from CDSE OData; non-COG JP2s) need HTTP range + resume + retries + checksum. An `httpx`/`aiohttp` range downloader is ~200 lines; stac-asset or EODAG's HTTP download plugin covers it already.
4. aria2 upstream is unmaintained; `aria2-next` is maintained but adds a daemon, RPC lifecycle, and packaging burden (Motrix Next's changelog shows the recurring port-conflict and orphan-process bugs) for no benefit in this domain.

Recommendation:

```
TransferEngine (interface): plan(assets) -> TransferJobs; run(jobs, progress) ; verify(job)
  ├─ GDALWindowTransfer   : COG asset + AOI window → local GeoTIFF/COG (default for COG hrefs)
  ├─ StacAssetTransfer    : whole asset via stac-asset clients (HTTP / S3 / MPC-signed / Earthdata)
  └─ EODAGTransfer        : whole product via EODAG (OData zipper, ordering/wait, extraction)
```

aria2/aria2-next: **unnecessary for v1**; may appear later as an optional backend for plain-HTTP bulk lists if measured to be faster, never as a dependency.

Verification: SHA-256 of every local file recorded; provider checksum compared when present (CDSE OData exposes MD5/BLAKE3; S3 multipart ETags are not MD5 — the Earth Search ETag `…-28` proves this, so do not treat ETag as a checksum).

## 14. Processing strategy

V1 operations, all GDAL/Rasterio, all recorded in provenance with tool version and exact parameters:
`clip_to_aoi`, `reproject` (to a project CRS chosen deterministically: UTM zone of AOI centroid unless user overrides), `select_bands`, `to_cog`, `build_vrt` (per date, per band), `organise` (`<dataset>/<YYYY-MM>/<date>_<tile>_<band>.tif`), `extract_metadata`. Optional: `monthly_composite` (median via Rasterio/numpy) — mark as experimental. Nothing sensor-specific beyond band aliases. SNAP/GRASS/QGIS remain the analysis layer; S1 comes analysis-ready (RTC) rather than us doing calibration/terrain correction.

## 15. Project-state / manifest design

Two representations, one source of truth:

- **`project.json`** — objective (template id + free text), AOI (path + WKT hash), period, project CRS, datasets declared (name, requirement id, status), settings.
- **Static STAC catalogue** under `<project>/catalog/` — one STAC Item per acquired asset set (geometry, datetime, `proj:*`, `eo:bands`, `file:size`, `file:checksum`, local `href`s, provider item id and source href in `derived_from`/`providers`). QGIS ≥ 3.40 opens this natively; pystac reads it; it *is* the index of local assets.
- **`recipes/<plan-id>.json`** — the approved `AcquisitionPlan` plus execution results: queries issued (endpoint, body, time), items considered/selected with reasons, transfers (bytes, duration, checksum, verification), processing steps (op, params, GDAL version, inputs → outputs), software versions. Re-runnable: `geofetch rerun recipes/<id>.json`.
- **SQLite** (`.index.sqlite`) — derived cache over the STAC items for fast gap queries (coverage per dataset × month × tile). Rebuildable from the catalogue; never the source of truth.

Gap analysis = `required(dataset, period, tiles) − present(catalogue)` computed in SQL/Shapely. "Import existing folder" = run GDAL metadata extraction over files, create STAC Items with `provenance: imported`.

Reproducibility export = `project.json + catalog/*.json + recipes/*.json` (kilobytes, no rasters).

## 16. UI / workflow concept

**Phase 1 UI is a CLI** (`geofetch init|plan|show|approve|run|status|gaps|rerun`) plus the plan rendered as readable text/JSON. Reason: the hypothesis under test is the planner + manifest, and a GUI is the most expensive, least informative part to build first. Every command emits JSON so the future GUI is a view over the same objects.

**Phase 2 desktop shell** (only after §20 experiments pass): Tauri + web frontend embedding the Python core as a sidecar, or a local FastAPI + browser UI; decide later on packaging evidence. Screens: Project overview (dataset status tiles), Map/AOI (draw/upload), Plan review (editable table, coverage map, size/quota bar, explanations, Approve/Run), Activity (transfers, processing, logs). Natural-language box is one entry point that produces a plan; the plan is always editable with normal controls. "Open in QGIS" = launch QGIS with the static catalogue/VRTs; a QGIS plugin is a later thin client, not a dependency.

## 17. MVP (smallest thing that tests the core hypothesis)

Scope, deliberately one sensor, one provider family, no GUI:

1. `geofetch init` — project dir, `project.json`, AOI from GeoJSON/GPKG/Shapefile (OGR), period, objective text.
2. Objective templates (YAML): `vegetation_timeseries`, `flood_change_detection_sar` — each lists requirements with **explanations**.
3. Intent interpreter: LLM (Claude via API, model configurable) → `StructuredIntent`; **also** a fully manual path (`--template vegetation_timeseries --bands red,nir --cloud-max 20 --step monthly`) so the LLM is optional from day one.
4. Planner for Sentinel-2 L2A on **Earth Search** (anonymous, COG, probed working): tile intersection, per-tile-per-window best-scene selection, composite-mode selection, coverage report, byte estimate (HEAD × area fraction + fixed overhead), prep steps, and a **feasibility verdict per window** — E1 showed that in July/August 2026 no combination of Sentinel-2 scenes reaches 80 % expected clear coverage of Karnataka; the plan must state this and propose alternatives (Sentinel-1, coarser daily optical, longer composite window) instead of silently proposing a cloudy mosaic.
5. Plan file + `geofetch show` (human-readable) + `geofetch approve`.
6. Transfer: `GDALWindowTransfer` for selected bands; SHA-256; resume by skipping verified outputs.
7. Prepare: clip, reproject to project UTM, COG, organise, per-month VRT.
8. Provenance: recipe + static STAC catalogue; `geofetch gaps`; `geofetch rerun`.
9. Import existing folder into the catalogue.

Add **Sentinel-1 RTC via Planetary Computer** as MVP+1 (signing via stac-asset's `PlanetaryComputerClient`), with the pre/post pairing rule set (same relative orbit + orbit state, VV+VH, nearest dates to event ± windows). This is justified because the flood use case is the second concrete workflow and MPC RTC removes the SNAP dependency; it also forces the transfer layer to handle signed URLs early.

Explicitly **not** in MVP: CDSE auth/quotas, EODAG adapter, rainfall/soil moisture providers, composites, desktop GUI.

## 18. Explicit non-goals

- Generic download manager features: URL lists, torrents, browser integration, speed scheduling, categories.
- Being an aria2 GUI.
- Remote-sensing analysis (indices, classification, InSAR, calibration): QGIS/Python/SNAP do this.
- Cloud processing backend or replacing openEO/GEE.
- Twenty providers in v1.
- Chat as the primary interface.
- Hosting or uploading user rasters anywhere.
- Custom STAC server, database server, or microservices.

## 19. Risks (answers to the "kill it" questions)

| Question | Answer | Mitigation |
|---|---|---|
| Does EODAG already solve enough? | No. It solves search/auth/download, not planning, coverage, state, provenance. Its filter abstraction leaked in today's probe. | Reuse it as an adapter; own normalisation + planning. |
| Do researchers prefer notebooks? | Many do. | Core is a Python package; the CLI/GUI are views. Notebook users get `geofetch.plan()` and the recipe format. |
| Do GIS analysts just want a QGIS plugin? | Some do; QGIS 4 broke the plugin ecosystem in 2026 and native STAC download is broken. | Static STAC output gives QGIS integration for free; a thin plugin can come later. |
| Cloud-first trend / EOPF Zarr? | Real. Large-area, many-year analyses belong in openEO/GEE. | Target AOI-scale local workflows (districts, basins, seasons), metered networks, offline reproducibility. Windowed transfer works on Zarr/COG alike; add a Zarr adapter when EOPF is operational. |
| Provider auth too fragmented? | Yes, if we try to cover all. | One anonymous provider first; stac-asset clients for the next two; never invent auth code. |
| Signed URLs vs aria2? | They defeat aria2; irrelevant since aria2 is out. | Re-sign inside `TransferEngine`. |
| Licensing? | Copernicus/Landsat/MPC open; Bhoonidhi mixed (Resourcesat open, Cartosat priced); GEE terms restrict commercial use (not used). | Record licence per collection in the manifest; no scraping. |
| Is LLM reasoning trustworthy? | Not for facts. | Type-level firewall (§11); manual path always available; every plan approved by a human. |
| Would users trust recommendations? | Only if explained and editable. | Explanations are rule-table text, not model prose; plan is a table. |
| Provider connector maintenance? | Real cost; the reason for stac-utils/EODAG reuse. | Two adapters, collection YAMLs, contract tests against live endpoints. |
| Five apps in one? | As originally worded, yes. | Cut to planner + manifest + thin transfer/prep orchestration (§17). |
| Sensor-specific post-processing? | Yes beyond clip/reproject/COG. | Stop there; consume ARD (RTC) rather than producing it. |
| Audience too small? | Unknown; niche but global (every EO course, agency, consultancy downloads scenes). | §20 experiments include interviews; a library/CLI has value even if the GUI never ships. |
| CLI/library vs GUI? | Library+CLI first is strictly better for validation; GUI is where the broader audience is. | Phase it. |
| Coverage-planning correctness | Wrong tile geometry or footprint handling gives wrong "gaps". | Use item geometries from STAC + Shapely; unit tests on known tiles; show coverage map. |

## 20. Validation experiments (before any GUI work)

E1 **Planner value** — **DONE, passed** (results in `docs/EXPERIMENTS.md`). Naive `cloud≤20` covers 40 % / 11.5 % / 0 % / 7.5 % of Karnataka in Jun/Jul/Aug/Sep 2026; the planner covers 98–99 % every month. Unexpected finding: expected *clear* coverage is only 42–74 % and July/August cannot reach 80 % with every scene combined — the plan needs a per-window feasibility verdict (added to §17).

E2 **Windowed transfer** — **DONE, passed on correctness, refined on speed.** Pixel-identical clips; byte reduction 1.6× (district ≈ tile) to 77× (10 km box); sequential `gdalwarp` fetch is latency-bound, parallel block reads (16 threads) beat the full download even for a tile-sized AOI (17 s vs 32 s). Area-fraction estimate within 7 % (+ ~2 MB fixed overhead per asset). CDSE whole-SAFE comparison not run (needs an account).

E3 **Reproducibility** — run a recipe on a second machine; compare STAC item ids and SHA-256s. Success: identical selection; checksums identical for windowed outputs given same GDAL version (record version; document non-bit-identical cases across GDAL versions).

E4 **Gap analysis on real folders** — import three existing practitioner project folders; check that detected coverage matches the owner's understanding.

E5 **Intent parsing** — 30 realistic prompts (EN + Indian-English phrasing), Claude → `StructuredIntent`; measure template accuracy and any attempt to emit forbidden fields (must be 0 by construction). Repeat with a local model for feasibility.

E6 **Users** — 5–8 interviews with drought/flood researchers and QGIS-centred analysts (NRSC/university/NGO circles). Ask them to narrate their last acquisition; show E1's plan output. Success: majority say the plan/coverage report and gap analysis would replace a manual step they currently do.

E7 **Sentinel-1 pairing** — for a known 2026 flood event, verify the pairing rules select same-orbit pre/post RTC scenes from MPC and that the transfer layer survives SAS expiry across a paused/resumed job.

Kill criteria: E1 shows no coverage improvement over naive filtering **or** E6 users don't recognise the problem → stop, and instead contribute coverage planning to Open Geodata Browser/EODAG as a library.

## 21. Verdict

**Qualified GO** — on the narrowed product: *project-first, reproducible EO acquisition & preparation*, delivered as a Python core + CLI first, desktop shell second.

**NO-GO** on: an aria2/IDM-style download manager (crowded, solved), an LLM-first "GIS agent" (research space, untrustworthy for facts, code-gen approaches already exist), a multi-provider v1, and a processing suite.

Why GO: the gap in §7 is real, deterministic, cheap to prototype on top of pystac-client + GDAL + stac-asset, verified today against live catalogues, and no maintained tool occupies it. Why qualified: the addressable audience and the value of the planner over a naive filter are assumptions until E1 and E6 are run; the cloud-first trend caps the ceiling to AOI-scale local workflows — which is exactly the segment the founder is in.

Next step, if accepted: implement only the MVP in §17, starting with E1/E2 as executable scripts that become the planner and transfer modules.

## 22. Sources

Primary docs and repos consulted (accessed 2026-09-15):
- EODAG: https://eodag.readthedocs.io/ · https://github.com/CS-SI/eodag/releases · providers page · download guide
- stac-asset: https://github.com/stac-utils/stac-asset · pystac-client: https://github.com/stac-utils/pystac-client
- CDSE: https://documentation.dataspace.copernicus.eu/APIs/S3.html · quotas news (2023-12-12) · openEO credit billing update (2026-03-02) · EOPF Zarr: https://zarr.eopf.copernicus.eu/ · https://developmentseed.org/blog/2026-02-13-eopf-explorer-launch/
- Planetary Computer: https://planetarycomputer.microsoft.com/docs/ · SAS API probed · Earth Copilot/Planetary Explorer: https://github.com/microsoft/Earth-Copilot
- QGIS STAC: https://changelog.qgis.org/en/version/3.40/ · https://www.lutraconsulting.co.uk/blogs/stac-in-qgis · QGIS 4 migration: https://plugins.qgis.org/docs/migrate-qgis4
- Open Geodata Browser: https://plugins.qgis.org/plugins/open_geodata_browser/ · STAC API Browser: https://plugins.qgis.org/plugins/qgis_stac/
- LLM-Find: https://arxiv.org/abs/2407.21024 · https://github.com/gladcolor/LLM-Find · Risk-aware agents: https://arxiv.org/html/2606.15077 · GIS Copilot: https://www.tandfonline.com/doi/full/10.1080/17538947.2025.2497489
- MCP: https://github.com/sparkgeo/geo-mcp-servers · https://glama.ai/mcp/servers/IBM/chuk-mcp-stac
- Kue: https://plugins.qgis.org/plugins/kue-ai/ · IntelliGeo: https://plugins.qgis.org/plugins/intelli_geo/
- Bhoonidhi: https://bhoonidhi.nrsc.gov.in/ · https://pypi.org/project/bhoonidhi-downloader/
- aria2 / aria2-next: https://github.com/aria2/aria2 · https://github.com/AnInsomniacy/aria2-next · Motrix Next: https://github.com/AnInsomniacy/motrix-next
- S1 RTC: https://hyp3-docs.asf.alaska.edu/guides/rtc_product_guide/ · MPC `sentinel-1-rtc` probed
- Reproducibility: EGUsphere 2025-5210 (barriers to reproducibility in geoscience) · arXiv 2506.08597 (provenance-aware openEO)
- Live probes: `docs/probes/stac_probe.py`, `docs/probes/coverage_probe.py`, `docs/probes/eodag_cloudcover_probe.py` (run with Python ≥ 3.12; the EODAG probe needs `pip install eodag`).
