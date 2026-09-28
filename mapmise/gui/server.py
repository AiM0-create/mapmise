"""Local GUI server: `mapmise gui` serves a small web app on 127.0.0.1 and opens it in the browser.

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

from mapmise import __version__, engine
from mapmise.catalog import Catalog
from mapmise.prepare import build_vrts, open_in_qgis, vector_files
from mapmise.project import Project
from mapmise.registry import load_sources
from mapmise.run import gaps
from mapmise.library import Library

STATIC = Path(__file__).parent / "static"
TOKEN = secrets.token_urlsafe(24)
WORKSPACE = Path.home() / "mapmise-projects"
NATIVE = False  # True while the app is shown in its own window rather than a browser tab
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
    return json.loads(project.aoi_file.read_text(encoding="utf-8"))


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

_PROGRESS = ("Finding ", "Found ", "Checking ")  # step messages shown live, not kept as plan notes


class _Cancelled(Exception):
    pass


def _prepare(body: dict, progress=None) -> dict:
    opts = engine.Options(workspace=str(WORKSPACE), allow_large=bool(body.get("allow_large")),
                          rules=[str(r) for r in body.get("rules") or []])
    for k in ("project", "place", "start", "end", "event"):
        if body.get(k):
            setattr(opts, k, body[k])
    opts.pick = int(body.get("pick") or 0)
    opts.skip = list(body.get("skip") or [])
    opts.use = [f"{k}={v}" for k, v in (body.get("use") or {}).items()]
    if body.get("mode") in ("single", "composite"):
        opts.mode = body["mode"]
    if body.get("aoi_geojson"):  # boundary uploaded in the browser
        f = Path(tempfile.mkdtemp(prefix="mapmise-aoi-")) / f"{(body.get('aoi_name') or 'area').rsplit('.', 1)[0]}.geojson"
        f.write_text(json.dumps(body["aoi_geojson"]), encoding="utf-8")
        opts.aoi = str(f)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    def log(msg: str) -> None:
        if progress:
            progress(msg)
        if not msg.startswith(_PROGRESS):
            notes.append(msg)
    r = engine.prepare(body["text"], opts, log=log)
    project = Project.load(Path(r["project"]))
    from mapmise.registry import load_ask_rules
    titles = {x.id: x.label for x in load_ask_rules()}
    return {**{k: v for k, v in r.items() if k != "plans"}, "notes": notes, "aoi": _aoi_geojson(project),
            "rule_titles": {rid: titles.get(rid, rid) for rid in r["rules"]},
            "plans": [_plan_row(pl, r["alternatives"]) for pl in r["plans"]], "library_here": _library_here(project)}


def _about() -> dict:
    import platform
    from mapmise.geo import user_dir
    folder = user_dir("data")
    folder.mkdir(parents=True, exist_ok=True)
    return {"version": __version__, "system": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "window": NATIVE, "log_folder": str(folder)}


def _redact(text: str) -> str:
    from mapmise.drivers.signing import redact
    return redact(text)


_plans_in_progress: dict[str, dict] = {}


def _start_prepare(body: dict) -> dict:
    """Plan in the background so the page can show each step and offer Cancel; poll /api/prepare-status."""
    job = {"id": uuid.uuid4().hex[:12], "messages": [], "done": False, "cancel": False, "started": __import__("time").time()}
    with _jobs_lock:
        _plans_in_progress[job["id"]] = job

    def progress(msg: str) -> None:
        if job["cancel"]:
            raise _Cancelled()
        job["messages"].append(_redact(msg))

    def work():
        try:
            job["result"] = _prepare(body, progress)
        except _Cancelled:
            job["error"], job["kind"] = "Planning was cancelled.", "cancelled"
        except engine.NeedsChoice as e:
            job["error"], job["kind"], job["choices"] = str(e), "choose", {"suggested": e.suggestions, "all": e.all}
        except engine.LargeArea as e:
            job["error"], job["kind"], job["area"] = str(e), "large", {"name": e.name, "km2": round(e.area_km2)}
        except engine.AskError as e:
            job["error"], job["kind"] = str(e), "ask"
        except Exception as e:  # noqa: BLE001 — surfaced to the page
            traceback.print_exc()
            job["error"], job["kind"] = _redact(f"{type(e).__name__}: {e}"), "error"
        finally:
            job["done"] = True
    threading.Thread(target=work, daemon=True).start()
    return {"job": job["id"]}


def _prepare_status(job_id: str) -> dict:
    import time as _t
    job = _plans_in_progress[job_id]
    out = {"done": job["done"], "step": job["messages"][-1] if job["messages"] else "Starting…",
           "seconds": round(_t.time() - job["started"])}
    for k in ("result", "kind", "area", "choices"):
        if k in job:
            out[k] = job[k]
    if "error" in job:  # "problem", not "error": the page treats an "error" field as a failed request
        out["problem"] = job["error"]
    return out


def _start_job(body: dict) -> dict:
    project = Project.load(Path(body["project"]))
    job = {"id": uuid.uuid4().hex[:12], "project": str(project.root), "plan_ids": list(body["plan_ids"]), "messages": [], "done": False}
    with _jobs_lock:
        _jobs[job["id"]] = job

    def work():
        try:
            job["result"] = engine.run(project, body["request_id"], job["plan_ids"], int(body.get("threads", 12)),
                                       progress=lambda ev: job["messages"].append(_redact(f"{ev['source']}: {ev['message'] if ev['message'] != 'done' else 'finished'}")))
        except Exception as e:  # noqa: BLE001 — surfaced to the page
            from mapmise.drivers.signing import redact
            job["error"] = redact(f"{type(e).__name__}: {e}")
            traceback.print_exc()
        finally:
            job["done"] = True
    threading.Thread(target=work, daemon=True).start()
    return {"job": job["id"]}


def _open_folder(path: Path) -> None:
    if sys.platform.startswith("win"):
        import os
        os.startfile(str(path))  # noqa: S606 — opens Explorer on a folder we created
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = f"mapmise/{__version__}"

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
        return secrets.compare_digest(self.headers.get("X-Mapmise-Token", ""), TOKEN)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in ("/", "/index.html"):
            html = ((STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
                    .replace("__VERSION__", __version__).replace("__NATIVE__", "true" if NATIVE else "false"))
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
            if u.path == "/api/earthdata":
                from mapmise import auth
                return self._json(auth.info().to_json())  # never the token itself
            if u.path == "/api/about":
                return self._json(_about())
            if u.path == "/api/sources":
                return self._json({sid: src.name for sid, src in load_sources().items()})
            if u.path == "/api/projects":
                return self._json({"workspace": str(WORKSPACE), "projects": _projects()})
            if u.path == "/api/status":
                p = Project.load(Path(q["project"]))
                return self._json({**engine.status(p), "aoi": _aoi_geojson(p)})
            if u.path == "/api/prepare-status":
                return self._json(_prepare_status(q["id"]))
            if u.path == "/api/job":
                return self._json(_job_view(_jobs[q["id"]]))
            if u.path == "/api/library":
                lib = Library()
                try:
                    return self._json(lib.summary())
                finally:
                    lib.close()
            if u.path == "/api/recipe":
                from mapmise import recipe
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
                return self._json({"markdown": f.read_text(encoding="utf-8") if f.exists() else ""})
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
                return self._json(_start_prepare(body))
            if u.path == "/api/prepare-cancel":
                job = _plans_in_progress.get(body.get("job", ""))
                if job:
                    job["cancel"] = True
                return self._json({"ok": bool(job)})
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
            if u.path == "/api/earthdata":
                from mapmise import auth
                if body.get("remove"):
                    return self._json({"removed": auth.clear()})
                if body.get("check"):
                    from mapmise.registry.check import check_source
                    r = check_source(load_sources()["hls-l30"])
                    return self._json({"ok": r.ok, "detail": r.detail})
                try:
                    return self._json(auth.save(str(body.get("token", ""))).to_json())
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if u.path == "/api/report-problem":
                from urllib.parse import urlencode
                a = _about()
                webbrowser.open("https://github.com/AiM0-create/mapmise/issues/new?" + urlencode(
                    {"template": "something-went-wrong.yml", "version": f"Mapmise {a['version']} · {a['system']}"}))
                return self._json({"ok": True})
            if u.path == "/api/open-log":
                _open_folder(Path(_about()["log_folder"]))
                return self._json({"ok": True})
            if u.path == "/api/show":
                from mapmise.gui import window as win
                return self._json({"ok": win.bring_to_front()})
            if u.path == "/api/save-recipe":
                from mapmise import recipe
                p = Project.load(Path(body["project"]))
                return self._json({"path": str(recipe.write(p))})
            if u.path == "/api/quit":
                running = [j for j in _jobs.values() if not j.get("done")]
                if running and not body.get("force"):
                    return self._json({"ok": False, "running": len(running)})
                if NATIVE:
                    from mapmise.gui import window as win
                    threading.Timer(0.2, win.close).start()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self._json({"ok": True})
        except engine.AskError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return self._json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)


def _running_file() -> Path:
    """Where a running Mapmise records its address. The desktop app keeps its own record (MAPMISE_RECORD), so a
    `mapmise gui` started from a terminal can never overwrite the one a second double-click relies on."""
    import os
    from mapmise.geo import user_dir
    return user_dir("data") / os.environ.get("MAPMISE_RECORD", "running.json")


def _running_jobs() -> int:
    with _jobs_lock:
        return sum(1 for j in _jobs.values() if not j.get("done"))


def already_running() -> dict | None:
    """{"url", "token", "mode"} of a mapmise app already running for this user, if one answers."""
    import urllib.request
    try:
        rec = json.loads(_running_file().read_text(encoding="utf-8"))
        with urllib.request.urlopen(rec["url"] + "static/app.css", timeout=2) as r:
            return rec if r.status == 200 else None
    except Exception:  # noqa: BLE001 — no record, stale record, or not answering: not running
        return None


def bring_forward(rec: dict) -> bool:
    """Ask a running app to show its window again. False if it has no window (browser mode)."""
    import urllib.request
    try:
        req = urllib.request.Request(rec["url"] + "api/show", data=b"{}", method="POST",
                                     headers={"X-Mapmise-Token": rec["token"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception:  # noqa: BLE001
        return False


def serve(port: int = 0, open_browser: bool = True, workspace: str | None = None, window: bool = False,
          smoke: bool = False) -> int:
    """Run the app. window=True shows it in its own window (falling back to the browser when the system has
    no web view); otherwise it opens in the browser. Returns an exit code."""
    global WORKSPACE, NATIVE
    if workspace:
        WORKSPACE = Path(workspace).expanduser().resolve()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    rec = _running_file()

    def record(mode: str) -> None:
        nonlocal rec
        try:
            rec.parent.mkdir(parents=True, exist_ok=True)
            rec.write_text(json.dumps({"url": url, "token": TOKEN, "mode": mode}), encoding="utf-8")
        except OSError:
            rec = None

    code = 0
    try:
        if window:
            from mapmise.gui import window as win
            if win.available():
                NATIVE = True
                record("window")
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                print(f"mapmise running in its own window ({url})", flush=True)
                try:
                    win.open_window(url, _running_jobs, on_closed=lambda: None, smoke=_smoke_check if smoke else None)
                    return _SMOKE.get("code", 0) if smoke else 0
                except win.WindowUnavailable as e:
                    NATIVE = False
                    print(f"no app window on this system ({e}); opening in the browser instead", flush=True)
                    if smoke:
                        return 3
                    httpd.shutdown()
                    httpd = ThreadingHTTPServer(("127.0.0.1", httpd.server_address[1]), Handler)
            elif smoke:
                print("smoke test: pywebview is not installed", flush=True)
                return 3
        record("browser")
        print(f"mapmise GUI running at {url}\nprojects folder: {WORKSPACE}\npress Ctrl+C or the Quit button to stop", flush=True)
        if open_browser:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        if NATIVE:  # the server ran beside the window in a background thread: stop it before closing its socket
            httpd.shutdown()
        httpd.server_close()
        if rec:
            rec.unlink(missing_ok=True)
        print("stopped", flush=True)
    return code


_SMOKE: dict = {}


def _smoke_check(window) -> None:
    """Release check: the page loads in the real window, the app script runs and the API answers."""
    import time
    try:
        window.events.loaded.wait(60)
        ok = projects = False
        for _ in range(60):  # the interface script runs, then its first API calls return
            ok = bool(window.evaluate_js("!!(window.MAPMISE_READY && document.querySelector('.segmented'))"))
            projects = bool(window.evaluate_js("window.MAPMISE_PROJECTS_LOADED === true"))
            if ok and projects:
                break
            time.sleep(0.5)
        print(f"smoke test: window loaded, app ready={ok}, API answered={projects}", flush=True)
        _SMOKE["code"] = 0 if ok and projects else 1
    except Exception as e:  # noqa: BLE001
        print(f"smoke test failed: {type(e).__name__}: {e}", flush=True)
        _SMOKE["code"] = 1
    finally:
        window.destroy()


_BACKGROUND: dict = {}


def start_in_background(workspace: str | None = None, native: bool = True) -> str:
    """Start the local server in a background thread (for the desktop app's own window); returns its URL."""
    global WORKSPACE, NATIVE
    if workspace:
        WORKSPACE = Path(workspace).expanduser().resolve()
    NATIVE = native
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    rec = _running_file()
    try:
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(json.dumps({"url": url, "token": TOKEN, "mode": "window" if native else "browser"}), encoding="utf-8")
    except OSError:
        rec = None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _BACKGROUND.update(httpd=httpd, rec=rec)
    print(f"mapmise running {'in its own window' if native else 'for the browser'} ({url})", flush=True)
    return url


def stop_background() -> None:
    httpd, rec = _BACKGROUND.pop("httpd", None), _BACKGROUND.pop("rec", None)
    if httpd:
        httpd.shutdown()
        httpd.server_close()
    if rec:
        rec.unlink(missing_ok=True)


def running_jobs() -> int:
    return _running_jobs()
