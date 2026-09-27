"""GUI server: serves the page with the session token, refuses API calls without it, lists projects."""
import json
import threading
import urllib.error
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from mapmise.gui import server
from mapmise.project import Project

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORKSPACE", tmp_path)
    Project.init(tmp_path / "demo", "Demo", "flood in demo", FIXTURES / "chitradurga.geojson", date(2026, 8, 1), date(2026, 8, 31))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(url, token=None):
    req = urllib.request.Request(url, headers={"X-Mapmise-Token": token} if token else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


def test_page_embeds_token_and_version(running):
    status, body = _get(running + "/")
    assert status == 200 and server.TOKEN.encode() in body and b"__TOKEN__" not in body


def test_api_requires_token(running):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(running + "/api/projects")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError):
        _get(running + "/api/projects", "wrong-token")


def test_projects_and_status(running, tmp_path):
    _, body = _get(running + "/api/projects", server.TOKEN)
    d = json.loads(body)
    assert [p["name"] for p in d["projects"]] == ["Demo"] and d["projects"][0]["start"] == "2026-08-01"
    _, body = _get(running + f"/api/status?project={tmp_path / 'demo'}", server.TOKEN)
    s = json.loads(body)
    assert s["name"] == "Demo" and s["items"] == 0 and s["aoi"]["type"] == "FeatureCollection"


def test_static_files_cannot_escape_the_static_folder(running):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(running + "/static/../server.py")
    assert e.value.code == 404
    status, _ = _get(running + "/static/vendor/leaflet/leaflet.js")
    assert status == 200


def test_prepare_rejects_empty_ask(running):
    req = urllib.request.Request(running + "/api/prepare", data=b'{"text": "  "}', method="POST",
                                 headers={"X-Mapmise-Token": server.TOKEN, "Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 400 and "Describe" in json.loads(e.value.read())["error"]


def test_quit_stops_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORKSPACE", tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/quit", data=b"{}", method="POST",
                                 headers={"X-Mapmise-Token": server.TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert json.loads(r.read())["ok"] is True
    t.join(timeout=5)
    assert not t.is_alive()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"X-Mapmise-Token": server.TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_page_says_whether_it_is_in_its_own_window(running, monkeypatch):
    _, body = _get(running + "/")
    assert b"GF_NATIVE = false" in body and b"__NATIVE__" not in body
    monkeypatch.setattr(server, "NATIVE", True)
    _, body = _get(running + "/")
    assert b"GF_NATIVE = true" in body


def test_source_names_for_the_interface(running):
    _, body = _get(running + "/api/sources", server.TOKEN)
    names = json.loads(body)
    assert names["cop-dem-glo-30"].startswith("Copernicus DEM") and len(names) >= 20


def test_recipe_is_saved_into_the_project(running, tmp_path):
    r = _post(running + "/api/save-recipe", {"project": str(tmp_path / "demo")})
    assert Path(r["path"]).exists() and Path(r["path"]).parent == (tmp_path / "demo").resolve()


def test_show_without_a_window_reports_false(running):
    assert _post(running + "/api/show", {})["ok"] is False
