from typing import Literal

from ninja import Router

from abst.models import Gemeinde, Partei
from abst.schema import (
    GemeindeSchema,
    ParteiGemeindeResultSchema,
    ParteiSchema,
    WahlenOptionSchema,
)
from abst.store import (
    get_wahlen_results,
    get_wahlen_results_lager,
    get_wahlen_results_parteigruppe,
)

router = Router()


@router.get("parteien", response=list[ParteiSchema])
def get_parteien(request):
    return Partei.objects.all().order_by("name")


@router.get("parteigruppen", response=list[WahlenOptionSchema])
def get_parteigruppen(request):
    gruppen = (
        Partei.objects.exclude(parteigruppen_id=None)
        .exclude(parteigruppen_name="")
        .values("parteigruppen_id", "parteigruppen_name")
        .distinct()
        .order_by("parteigruppen_name")
    )
    return [
        {"id": int(g["parteigruppen_id"]), "name": g["parteigruppen_name"]}
        for g in gruppen
    ]


@router.get("lager", response=list[WahlenOptionSchema])
def get_lager(request):
    lager = (
        Partei.objects.exclude(parteipolitische_lager_id=None)
        .exclude(parteipolitische_lager_name="")
        .values("parteipolitische_lager_id", "parteipolitische_lager_name")
        .distinct()
        .order_by("parteipolitische_lager_name")
    )
    return [
        {"id": int(l["parteipolitische_lager_id"]),
         "name": l["parteipolitische_lager_name"]}
        for l in lager
    ]


@router.get("parteien/{partei_id}/gemeinden/stand", response=list[GemeindeSchema])
def get_partei_gemeinden_stand(request, partei_id: int):
    if not Partei.objects.filter(partei_id=partei_id).exists():
        return []

    latest_stand = (
        Gemeinde.objects.select_related(
            "stand").order_by("-stand__date").first()
    )
    if not latest_stand:
        return []

    return Gemeinde.objects.filter(stand=latest_stand.stand).order_by("geo_id")


@router.get("parteien/{partei_id}/gemeinden", response=list[ParteiGemeindeResultSchema])
def get_partei_gemeinden(
    request,
    partei_id: int,
    mode: Literal["current", "last", "diff"] = "current",
):
    if not Partei.objects.filter(partei_id=partei_id).exists():
        return []

    result = get_wahlen_results(partei_id=partei_id, mode=mode)
    if result is None:
        return []
    return result.to_dicts()


@router.get(
    "parteigruppen/{parteigruppen_id}/gemeinden",
    response=list[ParteiGemeindeResultSchema],
)
def get_parteigruppen_gemeinden(
    request,
    parteigruppen_id: int,
    mode: Literal["current", "last", "diff"] = "current",
):
    result = get_wahlen_results_parteigruppe(
        parteigruppen_id=parteigruppen_id, mode=mode)
    if result is None:
        return []
    return result.to_dicts()


@router.get("lager/{lager_id}/gemeinden", response=list[ParteiGemeindeResultSchema])
def get_lager_gemeinden(
    request,
    lager_id: int,
    mode: Literal["current", "last", "diff"] = "current",
):
    result = get_wahlen_results_lager(lager_id=lager_id, mode=mode)
    if result is None:
        return []
    return result.to_dicts()
