from ninja import NinjaAPI

import abst.api

api = NinjaAPI()

api.add_router("/abst", router=abst.api.router)
