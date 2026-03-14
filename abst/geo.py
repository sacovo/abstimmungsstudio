from datetime import date

import requests
from django.core.files.base import ContentFile
from django.db import transaction

from abst.models import Gemeinde, GeoStand, Zaehlkreis


def import_geo_meta(url):
    data = requests.get(url).json()
    for resource in data["result"]["resources"]:
        coverage_date = date.fromisoformat(resource["coverage"])
        url = resource["url"]
        GeoStand.objects.get_or_create(date=coverage_date, defaults={"url": url})


def import_from_geojson(stand: GeoStand):
    gemeinden, kreise = fetch_geojson_eidg(stand)
    with transaction.atomic():
        Zaehlkreis.objects.filter(gemeinde__stand=stand).delete()
        Gemeinde.objects.filter(stand=stand).delete()

        Gemeinde.objects.bulk_create(gemeinden)
        Zaehlkreis.objects.bulk_create(kreise)


def fetch_geojson_eidg(stand: GeoStand) -> tuple[list[Gemeinde], list[Zaehlkreis]]:
    data = requests.get(stand.url)
    stand.document.save(
        f"geostand_{stand.date}.json", ContentFile(data.content), save=True
    )

    data = data.json()

    objects = data["objects"]
    voge_key = next(key for key in objects if "voge" in key)
    try:
        zaehlkreis_key = next(key for key in objects if "zaehlkreise" in key)
    except StopIteration:
        zaehlkreis_key = None

    gemeinden = {}
    zaehlkreise = []

    for feature in objects[voge_key]["geometries"]:
        properties = feature["properties"]
        if "id" in properties:
            db_gemeinde = Gemeinde.objects.filter(geo_id=int(properties["id"])).first()
            if not db_gemeinde:
                continue
            gemeinden[properties["id"]] = Gemeinde(
                name=db_gemeinde.name,
                geo_id=db_gemeinde.geo_id,
                kanton=db_gemeinde.kanton,
                kanton_id=db_gemeinde.kanton_id,
                stand=stand,
            )
            continue

        gemeinden[int(properties["vogeId"])] = Gemeinde(
            name=properties["vogeName"],
            geo_id=int(properties["vogeId"]),
            kanton=properties["kantName"],
            kanton_id=int(properties["kantId"]),
            stand=stand,
        )

    if zaehlkreis_key is not None:
        for feature in objects[zaehlkreis_key]["geometries"]:
            properties = feature["properties"]
            zaehlkreise.append(
                Zaehlkreis(
                    name=properties["name"],
                    geo_id=int(properties["id"]),
                    gemeinde=gemeinden[int(properties["vogeId"])],
                )
            )

    return list(gemeinden.values()), zaehlkreise


def get_geo_id_list(stand: GeoStand, kanton_id: int | None = None) -> list[int]:
    ids = []
    gemeinden = Gemeinde.objects.filter(stand=stand)
    kreise = Zaehlkreis.objects.filter(gemeinde__stand=stand)
    if kanton_id is not None:
        gemeinden = gemeinden.filter(kanton_id=kanton_id)
        kreise = kreise.filter(gemeinde__kanton_id=kanton_id)

    ids = list(gemeinden.order_by("geo_id").values_list("geo_id", flat=True))

    ids.extend(list(kreise.order_by("geo_id").values_list("geo_id", flat=True)))
    return ids
