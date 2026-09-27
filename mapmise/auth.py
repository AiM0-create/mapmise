"""NASA Earthdata Login: the user's own access token, kept on their computer.

Mapmise never asks for an Earthdata password. It uses a revocable access token the user generates at
https://urs.earthdata.nasa.gov (Generate Token). The token is read from, in order:

  1. the EARTHDATA_TOKEN environment variable, or
  2. a file in mapmise's per-user data folder, written by `mapmise earthdata login` or the app's settings,
     readable only by the user (mode 600 on macOS and Linux; the per-user AppData folder on Windows).

It is sent only to NASA Earthdata hosts (see drivers/signing.py) and never written to projects, plans,
catalogues, reports, recipes or logs.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ENV = "EARTHDATA_TOKEN"
TOKEN_PAGE = "https://urs.earthdata.nasa.gov/users/"  # Profile → Generate Token


class EarthdataLoginRequired(RuntimeError):
    def __init__(self, what: str = "This dataset"):
        super().__init__(f"{what} needs a free NASA Earthdata token. Generate one at https://urs.earthdata.nasa.gov "
                         "(Generate Token), then add it in Mapmise (Settings → NASA Earthdata) or with: mapmise earthdata login")


def token_file() -> Path:
    from mapmise.geo import user_dir
    return user_dir("data") / "earthdata-token"


def _claims(token: str) -> dict:
    """The token's public claims (expiry, issuer). Not a signature check — NASA verifies the token itself."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:  # noqa: BLE001
        return {}


@dataclass
class TokenInfo:
    present: bool
    source: str | None  # "environment" | "file" | None
    expires: float | None  # unix time
    valid_format: bool

    @property
    def expired(self) -> bool:
        return bool(self.expires and self.expires < time.time())

    def to_json(self) -> dict:
        return {"present": self.present, "source": self.source, "expired": self.expired, "valid_format": self.valid_format,
                "expires": time.strftime("%Y-%m-%d", time.gmtime(self.expires)) if self.expires else None}


def _read() -> tuple[str | None, str | None]:
    if os.environ.get(ENV, "").strip():
        return os.environ[ENV].strip(), "environment"
    f = token_file()
    try:
        t = f.read_text(encoding="utf-8").strip()
        return (t, "file") if t else (None, None)
    except OSError:
        return None, None


def token() -> str | None:
    """The usable token, or None if there is none or it has expired."""
    t, _ = _read()
    if not t:
        return None
    exp = _claims(t).get("exp")
    return None if exp and exp < time.time() else t


def info() -> TokenInfo:
    t, src = _read()
    if not t:
        return TokenInfo(False, None, None, False)
    c = _claims(t)
    return TokenInfo(True, src, c.get("exp"), bool(c) and "earthdata" in str(c.get("iss", "")).lower())


def validate(t: str) -> str:
    """Return the cleaned token or raise ValueError with a plain-language reason."""
    t = (t or "").strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    if t.count(".") != 2 or len(t) < 100:
        raise ValueError("That doesn't look like an Earthdata token. Copy the whole token from "
                         "urs.earthdata.nasa.gov → Generate Token (it starts with “eyJ”). Never use your password here.")
    c = _claims(t)
    if "earthdata" not in str(c.get("iss", "")).lower():
        raise ValueError("That token wasn't issued by NASA Earthdata Login.")
    if c.get("exp") and c["exp"] < time.time():
        raise ValueError("That token has expired. Generate a new one at urs.earthdata.nasa.gov.")
    return t


def save(t: str) -> TokenInfo:
    t = validate(t)
    f = token_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only from the moment it exists
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(t)
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    os.replace(tmp, f)
    return info()


def clear() -> bool:
    try:
        token_file().unlink()
        return True
    except FileNotFoundError:
        return False
