import threading
import time

import httpx

import mapmise.drivers.signing as sg


def _fake_get_factory(calls, statuses):
    def fake_get(url, **kw):
        calls.append(url)
        time.sleep(0.05)
        status = statuses.pop(0) if statuses else 200
        if status != 200:
            return httpx.Response(status, headers={"retry-after": "0"}, request=httpx.Request("GET", url))
        return httpx.Response(200, json={"token": "st=x&sig=y", "msft:expiry": "2099-01-01T00:00:00Z"}, request=httpx.Request("GET", url))
    return fake_get


def test_one_token_request_per_container_under_concurrency(monkeypatch):
    sg._cache.clear()
    calls = []
    monkeypatch.setattr(sg.httpx, "get", _fake_get_factory(calls, []))
    hrefs = [f"https://acct{i % 2}.blob.core.windows.net/cont/f{i}.tif" for i in range(32)]
    threads = [threading.Thread(target=sg.sign, args=(h,)) for h in hrefs]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 2
    assert sg.sign(hrefs[0]).endswith("?st=x&sig=y")


def test_rate_limit_is_retried(monkeypatch):
    sg._cache.clear()
    calls = []
    monkeypatch.setattr(sg.httpx, "get", _fake_get_factory(calls, [429, 429]))
    assert sg.sign("https://acct.blob.core.windows.net/c/x.tif").endswith("sig=y")
    assert len(calls) == 3


def test_non_pc_hrefs_untouched():
    assert sg.sign("https://sentinel-cogs.s3.us-west-2.amazonaws.com/a.tif") == "https://sentinel-cogs.s3.us-west-2.amazonaws.com/a.tif"
