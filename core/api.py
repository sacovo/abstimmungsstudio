from ninja import NinjaAPI

import abst.api
import abst.wahlen_api

api = NinjaAPI()

api.add_router("/abst", router=abst.api.router)
api.add_router("/wahlen", router=abst.wahlen_api.router)
