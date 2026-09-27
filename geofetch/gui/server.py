"""Local GUI server: `geofetch gui` serves a small web app on 127.0.0.1 and opens it in the browser.

Standard library only. The app is a view over the same engine the command line uses; every action
writes the same project files, plans, catalogue and report.

Safety: binds to 127.0.0.1 only, and every API call must carry the per-session token embedded in
the page, so other websites open in the browser cannot trigger downloads.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from geofetch import __version__, engine
from geofetch.catalog import Catalog
from geofetch.prepare import build_vrts, open_in_qgis, vector_files
from geofetch.project import Project
from geofetch.registry import load_sources
from geofetch.run import gaps
from geofetch.library import Library

STATIC = Path(__file__).parent / "static"
TOKEN = secrets.token_urlsafe(24)
WORKSPACE = Path.home() / "geofetch-projects"
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------- views of engine data

def _plan_row(pl: dict, alternatives: dict[str, list[str]]) -> dict:
    sources = load_sources()
    s = sources.get(pl["source"])
    key = f"{pl['needs'][0]['theme']}:{pl['needs'][0]['temporal']}" if pl["needs"] else ""
    worst = "ok"
    for w in pl["windows"]:
        v = w["verdict"]
        if v == "infeasible":
            worst = "infeasible"
        elif v in ("incomplete", "out-of-range") and worst != "infeasible":
            worst = "incomplete"
        elif v == "feasible-composite" and worst == "ok":
            worst = "composite"
    if pl["estimate"]["n_assets"] == 0 and not pl["acquire"]:
        worst = "skipped"
    return {
        "id": pl["id"], "source": pl["source"], "source_name": s.name if s else pl["source"],
        "licence": s.license if s else "", "resolution_m": s.resolution_m if s else None,
        "needs": [{"theme": n["theme"], "temporal": n["temporal"], "priority": n["priority"], "why": n["why"]} for n in pl["needs"]],
        "need_key": key, "alternatives": [a for a in alternatives.get(key, []) if a != pl["source"]],
        "files": pl["estimate"]["n_assets"], "present": pl["estimate"].get("present", 0), "from_library": pl["estimate"].get("from_library", 0),
        "bytes": pl["estimate"].get("to_fetch_bytes", pl["estimate"]["windowed_bytes"]), "size_known": pl["estimate"]["known"],
        "unknown_sizes": pl["estimate"].get("unknown", 0), "worst": worst,
        "windows": [{"label": w["label"], "verdict": w["verdict"], "text": w["verdict_text"]} for w in pl["windows"]],
        "fallback_for": pl.get("fallback_for"), "complement_for": pl.get("complement_for"),
        "explanations": pl.get("explanations", [])[:3],
    }


def _aoi_geojson(project: Project) -> dict:
    return json.loads(project.aoi_file.read_text())


def _projects() -> list[dict]:
    out = []
    if WORKSPACE.exists():
        for d in sorted(WORKSPACE.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if (d / "project.json").exists():
                p = Project.load(d)
                reqs = p.list_requests()
                out.append({"path": str(d), "name": p.meta.name, "area_km2": p.meta.aoi_area_km2, "start": p.meta.start, "end": p.meta.end,
                            "last_ask": reqs[-1]["ask"] if reqs else p.meta.objective, "requests": len(reqs)})
    return out


def _library_here(project: Project) -> list[dict]:
    """What other projects already hold over this project's area, grouped by source."""
    lib = Library()
    try:
        groups: dict[tuple[str, str], dict] = {}
        for h in lib.over(project.aoi().geometry):
            if h.project == project.root.resolve():
                continue
            g = groups.setdefault((h.source, str(h.project)), {"source": h.source, "project": h.project.name, "files": 0, "bytes": 0, "dates": set()})
            g["files"] += 1
            g["bytes"] += h.bytes
            if h.date:
                g["dates"].add(h.date)
        return [{**g, "dates": sorted(g["dates"])[:1] + sorted(g["dates"])[-1:] if g["dates"] else []} for g in groups.values()]
    finally:
        lib.close()


def _job_view(job: dict) -> dict:
    project = Project.load(Path(job["project"]))
    rows = []
    for pid in job["plan_ids"]:
        pl = project.load_plan(pid)
        n = pl["estimate"]["n_assets"] or len(pl["acquire"])
        missing = {(g["item"], g["asset"]) for g in gaps(project, pl)}
        # files land one by one but the catalogue entry is written per item: count finished files from the execution log too
        done_now = {(x["item"], x["asset"]) for x in pl.get("execution", {}).get("transfers", []) if x["status"] == "ok"}
        present = n - len(missing - done_now)
        rows.append({"plan": pid, "source": pl["source"], "total": n, "present": present, "status": pl["status"]})
    return {"id": job["id"], "done": job["done"], "error": job.get("error"), "result": job.get("result"),
            "messages": job["messages"][-6:], "plans": rows}


# ---------------------------------------------------------------- actions

def _prepare(body: dict) -> dict:
    opts = engine.Options(workspace=str(WORKSPACE))
    for k in ("project", "place", "start", "end", "event"):
        if body.get(k):
            setattr(opts, k, body[k])
    opts.pick = int(body.get("pick") or 0)
    opts.skip = list(body.get("skip") or [])
    opts.use = [f"{k}={v}" for k, v in (body.get("use") or {}).items()]
    if body.get("mode") in ("single", "composite"):
        opts.mode = body["mode"]
    if body.get("aoi_geojson"):  # boundary uploaded in the browser
        f = Path(tempfile.mkdtemp(prefix="geofetch-aoi-")) / f"{(body.get('aoi_name') or 'area').rsplit('.', 1)[0]}.geojson"
        f.write_text(json.dumps(body["aoi_geojson"]))
        opts.aoi = str(f)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    r = engine.prepare(body["text"], opts, log=notes.append)
    project = Project.load(Path(r["project"]))
    return {**{k: v for k, v in r.items() if k != "plans"}, "notes": notes, "aoi": _aoi_geojson(project),
            "plans": [_plan_row(pl, r["alternatives"]) for pl in r["plans"]], "library_here": _library_here(project)}


def _start_job(body: dict) -> dict:
    project = Project.load(Path(body["project"]))
    job = {"id": uuid.uuid4().hex[:12], "project": str(project.root), "plan_ids": list(body["plan_ids"]), "messages": [], "done": False}
    with _jobs_lock:
        _jobs[job["id"]] = job

    def work():
        try:
            job["result"] = engine.run(project, body["request_id"], job["plan_ids"], int(body.get("threads", 12)),
                                       progress=lambda ev: job["messages"].append(f"{ev['source']}: {ev['message'] if ev['message'] != 'done' else 'finished'}"))
        except Exception as e:  # noqa: BLE001 — surfaced to the page
            job["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            job["done"] = True
    threading.Thread(target=work, daemon=True).start()
    return {"job": job["id"]}


def _open_folder(path: Path) -> None:
    opener = "open" if sys.platform == "darwin" else "explorer" if sys.platform.startswith("win") else "xdg-open"
    subprocess.Popen([opener, str(path)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = f"geofetch/{__version__}"

    def log_message(self, fmt, *args):  # quiet by default
        pass

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, default=str).encode(), "application/json")

    def _authorised(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Geofetch-Token", ""), TOKEN)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in ("/", "/index.html"):
            html = (STATIC / "index.html").read_text().replace("__TOKEN__", TOKEN).replace("__VERSION__", __version__)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if u.path.startswith("/static/"):
            f = (STATIC / u.path[len("/static/"):]).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        if not u.path.startswith("/api/"):
            return self._send(404, b"not found", "text/plain")
        if not self._authorised():
            return self._json({"error": "forbidden"}, 403)
        try:
            if u.path == "/api/projects":
                return self._json({"workspace": str(WORKSPACE), "projects": _projects()})
            if u.path == "/api/status":
                p = Project.load(Path(q["project"]))
                return self._json({**engine.status(p), "aoi": _aoi_geojson(p)})
            if u.path == "/api/job":
                return self._json(_job_view(_jobs[q["id"]]))
            if u.path == "/api/library":
                lib = Library()
                try:
                    return self._json(lib.summary())
                finally:
                    lib.close()
            if u.path == "/api/recipe":
                from geofetch import recipe
                p = Project.load(Path(q["project"]))
                out = recipe.write(p)
                body = out.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Disposition", f'attachment; filename="{out.name}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            if u.path == "/api/report":
                p = Project.load(Path(q["project"]))
                f = p.root / "REPORT.md"
                return self._json({"markdown": f.read_text() if f.exists() else ""})
        except engine.AskError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return self._json({"error": "unknown endpoint"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if not self._authorised():
            return self._json({"error": "forbidden"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            if u.path == "/api/prepare":
                if not (body.get("text") or "").strip():
                    return self._json({"error": "Describe what you want to analyse first."}, 400)
                return self._json(_prepare(body))
            if u.path == "/api/run":
                return self._json(_start_job(body))
            if u.path == "/api/open-qgis":
                p = Project.load(Path(body["project"]))
                layers = build_vrts(Catalog(p.root, p.meta.name)) + vector_files(Catalog(p.root, p.meta.name))
                ok = open_in_qgis([p.aoi_file, *layers])
                return self._json({"ok": ok, "layers": len(layers), "error": None if ok else "QGIS was not found on this computer."})
            if u.path == "/api/open-folder":
                _open_folder(Path(body["project"]))
                return self._json({"ok": True})
        except engine.AskError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return self._json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)


def serve(port: int = 0, open_browser: bool = True, workspace: str | None = None) -> None:
    global WORKSPACE
    if workspace:
        WORKSPACE = Path(workspace).expanduser().resolve()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"geofetch GUI running at {url}\nprojects folder: {WORKSPACE}\npress Ctrl+C to stop")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
