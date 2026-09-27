"""NASA Earthdata: the user's token is stored privately, validated, and sent only to NASA hosts."""
import base64
import json
import os
import sys
import time

import httpx
import pytest

from mapmise import auth
from mapmise.drivers import signing
from mapmise.registry import Need
from mapmise.resolver import resolve_needs


def fake_token(exp_offset: int = 86400, iss: str = "https://urs.earthdata.nasa.gov") -> str:
    """A synthetic token with the shape of an Earthdata JWT; its signature is meaningless."""
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'typ': 'JWT', 'alg': 'RS256'})}.{enc({'iss': iss, 'exp': int(time.time()) + exp_offset, 'uid': 'test'})}.{'x' * 120}"


def test_token_is_saved_privately_and_read_back():
    t = fake_token()
    info = auth.save(t)
    assert info.present and info.source == "file" and not info.expired
    assert auth.token() == t
    if sys.platform != "win32":
        assert oct(os.stat(auth.token_file()).st_mode & 0o777) == "0o600"
    assert auth.clear() and auth.token() is None


@pytest.mark.parametrize("bad, why", [
    ("my-secret-password", "doesn't look like"),
    (fake_token(exp_offset=-60), "expired"),
    (fake_token(iss="https://example.com"), "wasn't issued by NASA"),
])
def test_passwords_expired_and_foreign_tokens_are_refused(bad, why):
    with pytest.raises(ValueError, match=why):
        auth.save(bad)
    assert not auth.token_file().exists()


def test_environment_variable_takes_precedence(monkeypatch):
    t = fake_token()
    monkeypatch.setenv("EARTHDATA_TOKEN", t)
    assert auth.token() == t and auth.info().source == "environment"


def test_only_protected_nasa_files_get_the_token():
    assert signing.needs_earthdata("https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/HLSL30.020/x/B04.tif")
    assert not signing.needs_earthdata("https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-public/x.tif")
    assert not signing.needs_earthdata("https://evil.example.com/earthdatacloud.nasa.gov/protected/x.tif")
    assert not signing.needs_earthdata("https://earthdatacloud.nasa.gov.evil.example.com/protected/x.tif")


def test_token_goes_to_nasa_but_not_to_the_storage_it_redirects_to():
    auth.save(fake_token())
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = request.headers.get("authorization")
        if request.url.host.endswith("earthdatacloud.nasa.gov"):
            return httpx.Response(307, headers={"location": "https://lp-prod.s3.us-west-2.amazonaws.com/B04.tif?X-Amz-Signature=abc"})
        return httpx.Response(206, headers={"content-range": "bytes 0-0/12345"}, content=b"\0")

    href = "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/HLSL30.020/T1/B04.tif"
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        url, size = signing.earthdata_resolve(href, client)
    assert seen["data.lpdaac.earthdatacloud.nasa.gov"].startswith("Bearer ")
    assert seen["lp-prod.s3.us-west-2.amazonaws.com"] is None
    assert url.startswith("https://lp-prod.s3.us-west-2.amazonaws.com/") and size == 12345
    signing._ed_cache.clear()


def test_missing_or_rejected_token_is_explained():
    signing._ed_cache.clear()
    href = "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/HLSL30.020/T2/B04.tif"
    with pytest.raises(auth.EarthdataLoginRequired, match="Earthdata token"):
        signing.earthdata_resolve(href)
    auth.save(fake_token())
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)), follow_redirects=True) as client:
        with pytest.raises(PermissionError, match="did not accept your token"):
            signing.earthdata_resolve(href, client)


def test_plans_explain_what_a_token_would_add(monkeypatch):
    need = Need("surface_water", "series", "recommended", "mapped water")
    bbox, start, end = [76.4, 13.85, 76.75, 14.15], "2025-06-01", "2025-08-31"
    [r] = resolve_needs([need], bbox, start, end, "IND")
    assert r.chosen is None and "Earthdata token" in r.unmet_reason
    monkeypatch.setattr("mapmise.auth.token", lambda: "t")
    [r] = resolve_needs([need], bbox, start, end, "IND")
    assert r.chosen.id == "opera-dswx-hls" and r.unmet_reason is None
