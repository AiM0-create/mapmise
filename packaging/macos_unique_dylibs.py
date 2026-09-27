"""macOS build step: give every package's bundled libraries a package-unique file name.

Wheels built with delocate keep plain library names, so rasterio, pyogrio and pyproj each ship their own
`libproj.25.x.dylib` — built differently (rasterio's renames its symbols). PyInstaller collects libraries by
file name, keeps one, and pyproj then loads rasterio's copy and fails ("Symbol not found: _proj_context_create").
Renaming each package's copies (`libproj…` → `pyproj_libproj…`) and relinking that package's own files keeps
every package on the exact libraries it was built with. Windows and Linux wheels already use unique names.

Run in the build environment before PyInstaller:  python packaging/macos_unique_dylibs.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

PACKAGES = ("rasterio", "pyogrio", "pyproj", "shapely", "numpy", "onnxruntime", "tokenizers", "PIL")


def _macho_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and not p.is_symlink() and p.suffix in (".so", ".dylib")]


def _deps(f: Path) -> list[str]:
    out = subprocess.run(["otool", "-L", str(f)], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
    return [line.strip().split(" (compatibility")[0] for line in out if line.strip()]


def unique(pkg: str) -> int:
    spec = importlib.util.find_spec(pkg)
    if not spec or not spec.submodule_search_locations:
        return 0
    root = Path(next(iter(spec.submodule_search_locations)))
    libs = root / ".dylibs"
    if not libs.is_dir():
        return 0
    prefix = f"{pkg.lower()}_"
    renames = {f.name: prefix + f.name for f in libs.glob("*.dylib") if not f.name.startswith(prefix)}
    if not renames:
        return 0
    for old, new in renames.items():
        (libs / old).rename(libs / new)
    changed = 0
    for f in _macho_files(root):
        args = []
        if f.parent == libs:
            args += ["-id", f"@rpath/{f.name}"]
        for dep in _deps(f):
            base = dep.rsplit("/", 1)[-1]
            if base in renames:
                args += ["-change", dep, dep[: -len(base)] + renames[base]]
        if args:
            subprocess.run(["install_name_tool", *args, str(f)], check=True, capture_output=True)
            subprocess.run(["codesign", "--force", "--sign", "-", str(f)], check=True, capture_output=True)  # re-sign after editing
            changed += 1
    print(f"{pkg}: {len(renames)} libraries renamed, {changed} files relinked")
    return len(renames)


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("only needed on macOS")
    for p in PACKAGES:
        unique(p)
