import datetime
from typing import Literal

from ninja import Field
from ninja.orm import ModelSchema
from ninja.schema import Schema

from abst.models import Abstimmungstag, Gemeinde, Kanton, Partei, Vorlage


class Result(Schema):
    final: bool

    ja_stimmen: int
    nein_stimmen: int
    anzahl_stimmberechtigte: int

    stimmbeteiligung: float
    ja_prozent: float


class AbstimmungstagSchema(ModelSchema):
    class Meta:
        model = Abstimmungstag
        fields = ["date"]


class KantonSchema(ModelSchema):
    class Meta:
        model = Kanton
        fields = ["name", "short", "kanton_id", "stimmen"]


class GemeindeSchema(ModelSchema):
    class Meta:
        model = Gemeinde
        fields = ["name", "geo_id", "kanton", "kanton_id"]


class GemeindeResult(Schema):
    timestamp: float
    geo_id: int
    vorlage_id: int

    geo_name: str
    kanton: str
    kanton_id: int

    result: Result | None = None


class VorlageSchema(Schema):
    name: str
    vorlagen_id: int

    finished: bool
    doppeltes_mehr: bool

    angenommen: bool

    ja_staende: float
    nein_staende: float

    has_zk: bool
    kantonal: bool = False
    region: str = "CH"

    result: dict | None = None


class VorlageListingSchema(ModelSchema):
    class Meta:
        model = Vorlage
        fields = [
            "name",
            "vorlagen_id",
            "finished",
            "doppeltes_mehr",
            "angenommen",
            "ja_staende",
            "nein_staende",
            "region",
            "result",
        ]

    date: datetime.datetime = Field(..., alias="tag.date")


class ResultsKantonSchema(Schema):
    kanton: int
    status: Literal["final", "prediction"]
    anzahl_stimmberechtigte: int
    ja_stimmen: int
    nein_stimmen: int


class ResultsTotalSchema(Schema):
    status: Literal["final", "prediction"]
    anzahl_stimmberechtigte: int
    ja_stimmen: int
    nein_stimmen: int


class ResultsHistorySchemaGemeinde(Schema):
    time: float
    status: Literal["final", "prediction"]
    ja_prozent: float
    stimmbeteiligung: float


class ResultsHistorySchemaKanton(Schema):
    time: float
    status: Literal["final", "prediction"]
    ja_stimmen: int
    nein_stimmen: int
    anzahl_stimmberechtigte: int
    kanton: str


class ResultsHistorySchemaTotal(Schema):
    time: float
    status: Literal["final", "prediction"]

    ja_stimmen: int
    nein_stimmen: int
    anzahl_stimmberechtigte: int


class ResultsGemeindeSchema(Schema):
    geo_id: int
    status: Literal["final", "prediction"]

    anzahl_stimmberechtigte: int
    ja_prozent: float
    ja_stimmen: int
    nein_stimmen: int
    stimmbeteiligung: float


class ParteiSchema(ModelSchema):
    class Meta:
        model = Partei
        fields = [
            "partei_id",
            "name",
            "kurzname",
            "parteigruppen_id",
            "parteigruppen_name",
            "parteipolitische_lager_id",
            "parteipolitische_lager_name",
        ]


class ParteiGemeindeResultSchema(Schema):
    geo_id: int
    value: float


class WahlenOptionSchema(Schema):
    id: int
    name: str


class ScatterMetricOptionSchema(Schema):
    id: str
    name: str


class ScatterScopeOptionSchema(Schema):
    id: Literal["partei", "parteigruppe", "lager"]
    name: str


class ScatterColorModeSchema(Schema):
    id: str
    name: str


class ScatterPointSchema(Schema):
    geo_id: int
    name: str
    kanton: str
    kanton_id: int

    status: Literal["final", "prediction"]

    ja_prozent: float
    stimmbeteiligung: float
    anzahl_stimmberechtigte: int
    wahlen_value: float | None = None
    abstimmung_value: float | None = None

    x_value: float | None = None
    y_value: float | None = None
    size_value: float | None = None
    color_value: float | str | None = None


class ScatterOptionsSchema(Schema):
    metrics: list[ScatterMetricOptionSchema]
    scopes: list[ScatterScopeOptionSchema]
    color_modes: list[ScatterColorModeSchema]
    parteien: list[WahlenOptionSchema]
    parteigruppen: list[WahlenOptionSchema]
    lager: list[WahlenOptionSchema]
