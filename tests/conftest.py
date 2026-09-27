import pytest


@pytest.fixture(autouse=True)
def _isolated_library(tmp_path, monkeypatch):
    """Never let tests read or write the user's real library, shared file cache or Earthdata token."""
    monkeypatch.setenv("MAPMISE_LIBRARY", str(tmp_path / "library.sqlite"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr("mapmise.auth.token_file", lambda: tmp_path / "data" / "earthdata-token")
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    monkeypatch.setenv("MAPMISE_CACHE", str(tmp_path / "cache"))
