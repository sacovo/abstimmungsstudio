import os
import django
import polars as pl
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from abst.models import Vorlage
from abst.store import get_influx_client
from django.conf import settings

vorlagen_ids = ['6810', '6800']
id_regex = "^(" + "|".join([str(v) for v in vorlagen_ids]) + ")$"

query = f'''
from(bucket: "{settings.INFLUX_BUCKET}")
    |> range(start: -100y)
    |> filter(fn: (r) => r._measurement == "result")
    |> filter(fn: (r) => r.vorlage_id == "6810" or r.vorlage_id == "6800")
    |> filter(fn: (r) => r._field == "ja_prozent" or r._field == "stimmbeteiligung")
    |> group(columns: ["geo_id", "vorlage_id", "_field"])
    |> last()
    |> keep(columns: ["geo_id", "vorlage_id", "_field", "_value"])
'''

with get_influx_client() as client:
    query_api = client.query_api()
    print("running query")
    result = query_api.query_data_frame(query)
    print("done query")
    
    if isinstance(result, list):
        if len(result) > 0:
            import pandas as pd
            
            all_dfs = []
            for r in result:
                cols = [c for c in ['geo_id', 'vorlage_id', '_field', '_value'] if c in r.columns]
                all_dfs.append(r[cols])
            
            result = pd.concat(all_dfs, ignore_index=True)
        else:
            import pandas as pd
            result = pd.DataFrame(columns=['geo_id', 'vorlage_id', '_field', '_value'])
            
    df = pl.from_pandas(result)
    
    pivoted = df.pivot(
        values="_value",
        index="geo_id",
        columns=["vorlage_id", "_field"],
    )
    
    print(pivoted.head())
    print("Shape:", pivoted.shape)
