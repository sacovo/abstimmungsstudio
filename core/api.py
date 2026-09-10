from ninja import NinjaAPI

import abst.api
import abst.export_api
import abst.wahlen_api

api = NinjaAPI()

api.add_router("/abst", router=abst.api.router)
api.add_router("/wahlen", router=abst.wahlen_api.router)
api.add_router("/export", router=abst.export_api.router)
