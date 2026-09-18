"""Objective layer: what data does an analytical objective need, and why.

Deterministic rule table. An objective template maps to a list of data requirements;
each requirement names a collection, bands, a temporal strategy, and the reasons it is
proposed. `supported` says whether this prototype can actually plan and fetch it — an
unsupported requirement is still listed, so "what am I missing?" stays honest.

Matching free text to a template is keyword-based here. An LLM could later produce the
same `StructuredIntent` (template id + parameters) — nothing downstream would change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class Requirement:
    id: str  # stable key inside a project, e.g. "vegetation"
    name: str
    priority: str  # required | recommended | optional
    provider: str | None
    collection: str | None
    bands: tuple[str, ...]
    temporal: str  # monthly | pre_post | single
    cloud_max: float | None
    why: tuple[str, ...]
    supported: bool
    unsupported_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    keywords: tuple[str, ...]  # regexes matched case-insensitively against the objective text
    requirements: tuple[Requirement, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


S2_VEG = Requirement(
    id="vegetation", name="Optical vegetation (Sentinel-2 L2A, red + NIR)", priority="required",
    provider="earth_search", collection="sentinel-2-l2a", bands=("red", "nir"), temporal="monthly", cloud_max=20,
    why=("10 m surface reflectance; red and NIR are all NDVI/vegetation-condition indices need",
         "5-day revisit gives several candidates per month, so the planner can pick the clearest per tile",
         "free, anonymous, cloud-optimised on Earth Search — only the AOI window is transferred"),
    supported=True,
)

TEMPLATES: dict[str, Template] = {
    "vegetation_drought": Template(
        id="vegetation_drought", name="Agricultural / vegetation drought assessment",
        keywords=(r"\bdrought", r"crop (stress|condition|failure)", r"vegetation (stress|condition|health)", r"\bmonsoon (failure|deficit)"),
        requirements=(
            S2_VEG,
            Requirement(
                id="rainfall", name="Rainfall (CHIRPS daily/pentad, 5 km)", priority="required",
                provider=None, collection="chirps", bands=(), temporal="monthly", cloud_max=None,
                why=("drought is a precipitation deficit first; vegetation response lags rainfall by weeks",
                     "CHIRPS is the standard long-record rainfall product for South Asia at 0.05°"),
                supported=False, unsupported_reason="no CHIRPS provider adapter in the prototype (UCSB/ClimateSERV, not STAC)",
            ),
            Requirement(
                id="soil_moisture", name="Soil moisture (SMAP L3 or ESA CCI)", priority="recommended",
                provider=None, collection="smap", bands=(), temporal="monthly", cloud_max=None,
                why=("root-zone / surface moisture is the physical link between rainfall deficit and crop stress",),
                supported=False, unsupported_reason="needs NASA Earthdata login and a CMR adapter (earthaccess); not built",
            ),
            Requirement(
                id="sar_backscatter", name="Sentinel-1 RTC backscatter (VV/VH)", priority="optional",
                provider="planetary_computer", collection="sentinel-1-rtc", bands=("vv", "vh"), temporal="monthly", cloud_max=None,
                why=("cloud-independent; the fallback when the optical planner reports an infeasible month",
                     "analysis-ready RTC on Planetary Computer, global since 2026"),
                supported=False, unsupported_reason="Planetary Computer signing and the S1 planner are not built (phase 4 candidate)",
            ),
        ),
        notes=("Monsoon months are often infeasible for optical data — expect the planner to say so; that is when SAR matters.",),
    ),
    "vegetation_timeseries": Template(
        id="vegetation_timeseries", name="Vegetation / crop time series (NDVI)",
        keywords=(r"\bndvi", r"vegetation (index|time ?series|monitoring)", r"crop (monitoring|phenology|growth|mapping)", r"\bgreenness"),
        requirements=(S2_VEG,),
    ),
    "flood_change_detection": Template(
        id="flood_change_detection", name="Flood extent: pre/post change detection",
        keywords=(r"\bflood", r"inundat", r"\bcyclone", r"\bpre[- ]?(and|/)?[- ]?post"),
        requirements=(
            Requirement(
                id="sar_pre_post", name="Sentinel-1 RTC pre/post pair (VV/VH, same relative orbit)", priority="required",
                provider="planetary_computer", collection="sentinel-1-rtc", bands=("vv", "vh"), temporal="pre_post", cloud_max=None,
                why=("floods happen under cloud; SAR sees through it",
                     "open water is a strong low-backscatter signal in VV",
                     "pre/post scenes must share relative orbit and orbit direction so geometry, not the flood, is what stays constant"),
                supported=False, unsupported_reason="Sentinel-1 pairing rules and Planetary Computer signing are not built (phase 4 candidate)",
            ),
            Requirement(
                id="optical_pre_post", name="Sentinel-2 L2A pre/post (red, green, NIR, SWIR)", priority="recommended",
                provider="earth_search", collection="sentinel-2-l2a", bands=("red", "green", "nir", "swir16"), temporal="pre_post", cloud_max=30,
                why=("for validation and mapping where a clear post-event scene exists (NDWI/MNDWI water indices)",
                     "the planner will say whether a clear post-event scene exists at all"),
                supported=True,
            ),
        ),
        notes=("Requires an event date: `geofetch plan --event YYYY-MM-DD` builds the pre/post windows.",),
    ),
    "surface_water": Template(
        id="surface_water", name="Surface water extent (reservoirs, lakes, wetlands)",
        keywords=(r"\b(reservoir|lake|wetland|tank)s?\b", r"surface water", r"water (extent|body|bodies|spread)"),
        requirements=(
            Requirement(
                id="optical_water", name="Sentinel-2 L2A (green, NIR, SWIR)", priority="required",
                provider="earth_search", collection="sentinel-2-l2a", bands=("green", "nir", "swir16"), temporal="monthly", cloud_max=20,
                why=("NDWI (green/NIR) and MNDWI (green/SWIR) separate water from land at 10–20 m",),
                supported=True,
            ),
        ),
    ),
}


@dataclass
class Match:
    template: Template
    score: int
    hits: list[str]


def match_objective(text: str) -> list[Match]:
    """Templates whose keywords occur in the objective text, best first. Empty if nothing matches."""
    out = []
    for t in TEMPLATES.values():
        hits = [k for k in t.keywords if re.search(k, text, re.IGNORECASE)]
        if hits:
            out.append(Match(t, len(hits), hits))
    return sorted(out, key=lambda m: -m.score)


def resolve(text: str, template_id: str | None = None) -> tuple[Template | None, list[Match]]:
    """Explicit template id wins; otherwise the unique best keyword match; None if ambiguous or no match."""
    if template_id:
        if template_id not in TEMPLATES:
            raise KeyError(f"unknown template {template_id!r}; known: {', '.join(TEMPLATES)}")
        return TEMPLATES[template_id], []
    matches = match_objective(text)
    if not matches:
        return None, matches
    if len(matches) > 1 and matches[0].score == matches[1].score:
        return None, matches  # ambiguous: make the user choose
    return matches[0].template, matches
