"""mapmise — local, reproducible acquisition of open geospatial data for an analysis."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mapmise")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.1.0a8"
