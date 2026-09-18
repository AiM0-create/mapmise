import json, urllib.request, sys, time
# Karnataka-ish bbox (rough), 2026 monsoon
bbox=[74.0,11.5,78.6,18.5]; dt="2026-07-01T00:00:00Z/2026-08-31T23:59:59Z"
def post(url, body):
    req=urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type":"application/json","Accept":"application/geo+json"})
    t=time.time(); r=urllib.request.urlopen(req, timeout=60); d=json.load(r); return d, time.time()-t
for name,url,coll,extra in [
 ("EarthSearch S2 L2A","https://earth-search.aws.element84.com/v1/search","sentinel-2-l2a",{"query":{"eo:cloud_cover":{"lt":20}}}),
 ("EarthSearch S1 GRD","https://earth-search.aws.element84.com/v1/search","sentinel-1-grd",{}),
 ("CDSE S2 L2A","https://stac.dataspace.copernicus.eu/v1/search","sentinel-2-l2a",{}),
 ("CDSE S1 GRD","https://stac.dataspace.copernicus.eu/v1/search","sentinel-1-grd",{}),
 ("MPC S2 L2A","https://planetarycomputer.microsoft.com/api/stac/v1/search","sentinel-2-l2a",{"query":{"eo:cloud_cover":{"lt":20}}}),
]:
    try:
        body={"collections":[coll],"bbox":bbox,"datetime":dt,"limit":3,**extra}
        d,dt_=post(url,body)
        feats=d.get("features",[]); ctx=d.get("context") or d.get("numberMatched") or d.get("numMatched")
        print(f"== {name}: {len(feats)} returned in {dt_:.1f}s; matched={ctx}")
        if feats:
            f=feats[0]; p=f["properties"]; a=f["assets"]
            print("  id:",f["id"]); print("  props:",{k:p.get(k) for k in ["datetime","eo:cloud_cover","sat:orbit_state","sat:relative_orbit","sar:polarizations","proj:epsg","proj:code","s2:tile_id","grid:code","mgrs:utm_zone"] if k in p})
            print("  assets:",list(a.keys())[:25])
            for k in ["B04","red","B08","nir","vv","vh","PRODUCT","B04_10m"]:
                if k in a:
                    print(f"   {k}: href={a[k]['href'][:90]} size={a[k].get('file:size')} type={a[k].get('type')}")
    except Exception as e:
        print(f"== {name}: ERROR {type(e).__name__}: {str(e)[:200]}")
