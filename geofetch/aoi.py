"""Area-of-interest loading and geometry helpers.

All area computations use an equal-area CRS (EPSG:6933) so that coverage
fractions are meaningful regardless of latitude.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

EQUAL_AREA_CRS = "EPSG:6933"

_to_equal_area = Transformer.from_crs("EPSG:4326", EQUAL_AREA_CRS, always_xy=True).transform


def to_equal_area(geom: BaseGeometry) -> BaseGeometry:
    """Project a WGS84 geometry to the equal-area CRS."""
    return transform(_to_equal_area, geom)


def area_km2(geom_wgs84: BaseGeometry) -> float:
    return to_equal_area(geom_wgs84).area / 1e6


@dataclass(frozen=True)
class AOI:
    name: str
    geometry: BaseGeometry  # WGS84, single (multi)polygon
    source: str

    @property
    def bbox(self) -> list[float]:
        return list(self.geometry.bounds)

    @property
    def area_km2(self) -> float:
        return area_km2(self.geometry)

    @property
    def utm_epsg(self) -> int:
        """Deterministic project CRS: UTM zone of the AOI centroid."""
        c = self.geometry.centroid
        zone = int((c.x + 180) // 6) + 1
        return (32600 if c.y >= 0 else 32700) + zone


def load_aoi(path: str | Path, name: str | None = None) -> AOI:
    """Load any OGR-readable vector file; dissolve all features into one geometry."""
    path = Path(path)
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise ValueError(f"{path} has no CRS")
    gdf = gdf.to_crs("EPSG:4326")
    geom = unary_union(gdf.geometry.values)
    if geom.is_empty:
        raise ValueError(f"{path} contains no geometry")
    return AOI(name=name or path.stem, geometry=geom, source=str(path))
