import json, urllib.request, time
bbox=[74.0,11.5,78.6,18.5]
def post(url, body):
    req=urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))
# MPC S1 RTC over Karnataka, 2026
d=post("https://planetarycomputer.microsoft.com/api/stac/v1/search",{"collections":["sentinel-1-rtc"],"bbox":bbox,"datetime":"2026-06-01T00:00:00Z/2026-08-31T23:59:59Z","limit":5})
print("MPC S1 RTC Karnataka Jun-Aug 2026 items(first page):",len(d["features"]))
for f in d["features"][:3]: print("  ",f["id"],f["properties"].get("sat:relative_orbit"),f["properties"].get("sat:orbit_state"))
# Count S2 L2A scenes Jul-Aug 2026 over Karnataka with cloud<20 on EarthSearch, paginate
url="https://earth-search.aws.element84.com/v1/search"
body={"collections":["sentinel-2-l2a"],"bbox":bbox,"datetime":"2026-07-01T00:00:00Z/2026-08-31T23:59:59Z","limit":100,"query":{"eo:cloud_cover":{"lt":20}},"fields":{"include":["id","properties.datetime","properties.eo:cloud_cover","properties.grid:code"],"exclude":["assets","geometry"]}}
d=post(url,body); print("EarthSearch S2 <20% cloud Jul-Aug 2026 matched:",d["context"]["matched"])
tiles=sorted({f["properties"]["grid:code"] for f in d["features"]}); print("  distinct MGRS tiles in page:",len(tiles))
body["query"]={"eo:cloud_cover":{"lt":100}}; body["limit"]=1
d=post(url,body); print("EarthSearch S2 all cloud Jul-Aug 2026 matched:",d["context"]["matched"])
