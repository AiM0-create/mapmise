import eodag, logging
from eodag import EODataAccessGateway
logging.disable(logging.CRITICAL)
dag=EODataAccessGateway()
g={"lonmin":76.5,"latmin":14.0,"lonmax":77.0,"latmax":14.5}
for kw in ({"eo_cloud_cover":20},{"eo_cloud_cover":{"lt":20}},{"eo_cloud_cover":{"lte":20}}):
    try:
        res=dag.search(collection="S2_MSI_L2A_COG",provider="earth_search",geom=g,start="2026-08-01",end="2026-08-31",count=True,**kw)
        print(kw,"-> matched",res.number_matched, "clouds:", [round(p.properties.get("cloudCover"),1) for p in res[:5]])
    except Exception as e: print(kw,"ERR",type(e).__name__,str(e)[:120])
