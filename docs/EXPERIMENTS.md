# Validation experiments — results

Companion to `PRODUCT_DISCOVERY.md` §20. All numbers measured 2026-09-15 from this machine
(Linux, GDAL 3.12, ~7 MB/s to `sentinel-cogs.s3.us-west-2.amazonaws.com`). Scripts under
`experiments/`, reusable code under `geofetch/`. Reproduce with:

```bash
uv pip install -e . && .venv/bin/python experiments/e1_planner.py && .venv/bin/python experiments/e2_transfer.py && .venv/bin/python experiments/e2b_transfer_scaling.py && .venv/bin/python experiments/e2c_parallel_windows.py
```

AOIs: Karnataka ADM1 (191,775 km², geoBoundaries gbOpen, CC-BY 4.0) and Chitradurga ADM2 (8,448 km²).

## E1 — Planner value (PASSED, with a finding that changes the product)

Sentinel-2 L2A on Earth Search, 2026-06-01 → 2026-09-15, bands red + nir. 2,586 items in bbox (40 s, 26 pages); 37 MGRS tiles intersect the AOI.

| Month | Scenes avail. | **Naive `cloud ≤ 20 %`** scenes / observed / clear | **Planner** (best scene per tile) observed / clear / flagged | **Composite** (target 80 % clear) scenes / expected clear |
|---|---|---|---|---|
| 2026-06 | 435 | 35 / 40.1 % / 32.6 % | 99.3 % / 73.6 % / 20 of 37 | 72 / 86.6 % |
| 2026-07 | 432 | 2 / 11.5 % / 8.4 % | 99.2 % / 45.0 % / 35 of 37 | 294 (all) / 69.3 % |
| 2026-08 | 426 | 1 / **0.0 %** / 0.0 % | 97.9 % / 42.3 % / 36 of 37 | 275 (all) / 77.3 % |
| 2026-09 (to 15th) | 169 | 4 / 7.5 % / 5.1 % | 99.3 % / 61.2 % / 35 of 37 | 97 / 81.2 % |

"Observed" = union of selected footprints ∩ AOI / AOI. "Clear" = Σ tile-exclusive area × footprint coverage × (1 − scene cloud %) / AOI — an expectation from catalogue metadata, not a pixel mask.

Findings:
1. **The naive filter every browser offers is useless for a monsoon time series** (0 % of Karnataka in August). Success criterion (≥ 95 % observed coverage where any scene exists) is met by the planner every month.
2. **But the planner cannot manufacture clear pixels.** Best-scene-per-tile yields 42–74 % expected clear; 126 of 148 selections exceed the 20 % threshold and are flagged with the reason. In July and August *every available scene* combined (294 / 275) still falls short of 80 % expected clear. This is a physical limit of optical data in the monsoon and the plan must say so and propose alternatives (Sentinel-1 backscatter, coarser-resolution daily optical, two-month composites) rather than silently proposing a cloudy mosaic. → Product requirement: plans carry an explicit *feasibility verdict* per window.
3. Byte estimates (296 HEAD requests, 18 s; 1,476 for composite, 87 s): single-scene plan 65.1 GB full COGs / 34.8 GB AOI-windowed; composite plan 738 scenes, 245 GB full / 144 GB windowed. HEAD-per-asset is acceptable at this scale but should be cached and parallel (it is).
4. Earth Search returns `Accept-Ranges: bytes`, no `file:size`, multipart ETags (not MD5).

## E2 — Windowed transfer (PASSED on correctness; hypothesis refined on speed)

Scene `S2B_43PFR_20260604_0_L2A`, band red (B04, 233 MB COG), clipped with `gdalwarp -cutline` over `/vsicurl/`.

**Correctness:** the windowed clip is pixel-identical (same SHA-256, same grid, 16180 × 10793 px, EPSG:32643) to clipping the fully downloaded file. Success.

**Bytes and time** (E2b; area-fraction estimate = full size × AOI∩footprint / footprint):

| AOI | km² | AOI / tile | Downloaded | HTTP ranges | Time | Reduction (bytes) | Estimate |
|---|---|---|---|---|---|---|---|
| full COG (baseline) | — | 100 % | 233.2 MB | 1 stream | 32.2 s @ 7.2 MB/s | 1× | — |
| Chitradurga district | 8,448 | 59.2 % | 147.3 MB | 41 | 33.6 s @ 4.4 MB/s | 1.6× | 138.0 MB |
| 30 km box | 899 | 7.5 % | 18.6 MB | 6 | 12.8 s | 12.6× | 17.4 MB |
| 10 km box | 100 | 0.8 % | 3.0 MB | 3 | 3.8 s | 76.7× | 1.9 MB |

Raising `CPL_VSIL_CURL_CHUNK_SIZE` to 5 MB made every case slower and heavier (over-fetch).

**Parallel block reads** (E2c; rasterio, one dataset handle per thread, 100 blocks of 1024²) for the district window:

| threads | 1 | 4 | 8 | 16 |
|---|---|---|---|---|
| wall time | 41.8 s | 22.4 s | 19.2 s | **17.0 s** |

Findings:
1. The "≥ 5× reduction" success criterion holds for AOIs below ~15 % of a tile (taluk/watershed/field-site scale) and fails for district-and-larger AOIs, where a tile is mostly inside the AOI.
2. Sequential range fetching (`gdalwarp` on `/vsicurl/`) is latency-bound (1–4 MB/s here) and can be *slower* than a single full stream. With 8–16 parallel block reads the district window completes in 17 s vs 32 s full download — windowed transfer wins at every scale **only if parallelised**.
3. The area-fraction estimate is a good predictor (within 7 % for the district and 30 km box) plus a ~1–2 MB fixed overhead (headers, IFDs, overviews) per asset. Use `estimate = size × fraction + 2 MB`.
4. Fixed overhead per asset (3 HTTP requests minimum) means many tiny AOIs across many scenes are dominated by latency; batching/keep-alive matters more than bandwidth.

Design consequences for `TransferEngine` (supersedes PRODUCT_DISCOVERY §13 detail):
- `GDALWindowTransfer` should be implemented as parallel block reads (rasterio/GDAL with N handles) writing a local COG, not as a single `gdalwarp` invocation.
- Strategy is chosen per asset from `AOI∩footprint / footprint`: windowed always wins on bytes; on time it wins once parallel. Whole-file streaming remains the path for non-COG assets (JP2, SAFE zips).
- Byte estimates and quota checks can be made before approval with one HEAD per asset (cached).

## Not yet run
E3 reproducibility (needs a second machine / GDAL version), E4 gap analysis on real folders, E5 intent parsing (LLM deliberately excluded for now), E6 user interviews (owner's task), E7 Sentinel-1 pairing on MPC RTC.

## Prototype status (2026-09-18)

Phases 1–2 of the plan in the 2026-09-17 session are built and tested (`README.md`):

- `init / plan / show / run / status` CLI; project store; cached discovery; planner with per-window verdicts; HEAD-based estimate; parallel windowed fetch → AOI-clipped COG in the project CRS; static STAC catalogue with sha256; resumable execution recorded in the plan file.
- Verified: windowed output is pixel-identical to the full download on the source grid (gdalwarp `-crop_to_cutline` without `-tap` resamples, which is why the E2 clips differed from a raw read; the executor reads on the source grid and never resamples unless the project CRS differs).
- Chitradurga, June 2026, 30 km box: plan 1 s (cached), run 32 s for 4 assets, rerun transfers nothing, pystac resolves every href, QGIS-readable catalogue.
- Byte counting inside the parallel reader was dropped (GDAL debug output does not reach rasterio's logger from worker threads); sizes are estimated from HEAD × tile fraction + 2 MB, calibrated in E2b.

Phase 3 (recipe re-run on a second machine, `import` of an existing folder) is not built. E6 (user interviews) is the remaining kill-criterion.
