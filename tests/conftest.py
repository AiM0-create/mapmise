import pytest


@pytest.fixture(autouse=True)
def _isolated_library(tmp_path, monkeypatch):
    """Never let tests read or write the user's real library or shared file cache."""
    monkeypatch.setenv("GEOFETCH_LIBRARY", str(tmp_path / "library.sqlite"))
    monkeypatch.setenv("GEOFETCH_CACHE", str(tmp_path / "cache"))
