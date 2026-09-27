# Contributing to mapmise

mapmise's knowledge lives in two YAML files, not in code. Most contributions are one entry in one of them.

| You want to… | Edit | Code needed? |
|---|---|---|
| add an open dataset | `mapmise/registry/sources.yaml` | no — if it is reachable by an existing driver |
| teach mapmise a new kind of ask | `mapmise/registry/asks.yaml` | no |
| support a new *way* of reaching data | `mapmise/drivers/` | yes — open an issue first |

## Add a dataset

1. **Read the provider's own documentation.** Endpoint, collection or URL pattern, licence, resolution, time span, coverage, nodata. Write the entry from that — never copy an entry from another tool's catalogue. Facts are free to use; other people's curated files are not.
2. **Describe what the data *is*, not what it is for.** Pick themes from the vocabulary in `mapmise/registry/__init__.py` (`optical`, `sar`, `elevation`, `water`, `vegetation`, `landcover`, `builtup`, `population`, `precipitation`, `temperature`, `soil`, `forest`, `fire`, `transport`, `facilities`, …). The ask rules connect analyses to themes.
3. **Choose the shape.** `layer` (static), `series` (dated scenes or products), `query` (live vector API).
4. **Choose the driver.** `stac` (any STAC API; Planetary Computer signing is automatic), `http` (`window` for cloud-optimised GeoTIFFs, `file_per_window` for one file per month, `tile_grid` for lat/lon tiles, `vector` for GeoJSON), `overpass` (OpenStreetMap).
5. **Prove it works:**

   ```bash
   mapmise sources --check your-source-id
   ```

   This queries the source live the way your entry says, and checks every declared asset exists and answers. A pull request adding a source must pass it.
6. **Try it in a real ask** with `--dry-run` and include the plan table in your pull request.

A minimal STAC entry:

```yaml
- id: modis-lst-8day
  name: MODIS land surface temperature, 8-day, 1 km (MOD11A2/MYD11A2 v6.1)
  themes: [temperature]
  kind: raster
  shape: series
  resolution_m: 1000
  temporal: {type: series, from: "2000-02", to: null, revisit_days: 8}
  coverage: global
  license: "No restrictions (NASA LP DAAC)"
  analysis_ready: true
  cloud_dependent: false
  description: Day and night land surface temperature.
  access:
    driver: stac
    provider: planetary_computer
    collection: modis-11A2-061
    assets: {lst_day: LST_Day_1km, lst_night: LST_Night_1km}
    default_assets: [lst_day, lst_night]
    group_by: modis:tile-id
    planner: products
```

Getting the details right matters more than adding many entries. In particular:

- `temporal.to` — when a product stops being published, say so. The resolver uses it to avoid offering data that does not exist, and `--check` will catch an ongoing claim that is false.
- `analysis_ready` — only `true` if it can be used without calibration or processing. mapmise never processes data.
- `nodata` — set it when the product declares none and 0 is a valid value.
- `license` — the provider's terms, in their words.

## Teach a new ask

An ask rule maps words to generic data needs:

```yaml
- id: heat
  keywords: ["\\bheat", "temperature", "\\blst\\b"]
  needs:
    - {theme: temperature, temporal: series, priority: required,    why: "land surface temperature day and night"}
    - {theme: builtup,     temporal: static, priority: recommended, why: "built-up land drives urban heat"}
```

- `temporal`: `static`, `series`, or `pair` (before/after an event).
- `why` is shown to the user. Write it for someone who is not an expert.
- Rules compose: an ask is the union of every rule it matches. Prefer small rules over one big one.
- `examples:` are natural phrasings of the ask. The built-in AI matches questions that use none of the
  keywords by meaning against them, so add the ways people really phrase this kind of question.
- After changing rules or examples, run `python experiments/e5_ai_understanding.py`: it reports how many
  questions in `tests/fixtures/asks_eval.yaml` are understood, with keywords only and with the AI. Don't copy
  evaluation questions into `examples:` — that would make the score meaningless.
- `mapmise rules` lists the current vocabulary.

## Development

```bash
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q          # offline tests
.venv/bin/mapmise sources --check     # live checks of every registry entry (slow; needs network)
```

Design rules that reviews will hold you to:

- **Deterministic first.** Facts — scene ids, sizes, dates, coverage — come from catalogues and computation, never guesses.
- **Nothing downloads on a suggestion.** Plans are shown with reasons and sizes and approved.
- **No per-analysis code.** If a new kind of ask needs code, the engine is missing a generic capability; add that instead.
- **Say what you could not do.** Unmet needs, infeasible windows and uncovered years are reported, not hidden.
- **Reuse mature libraries** (GDAL/rasterio, pystac-client, Shapely, pyproj). Do not reimplement GIS fundamentals.
