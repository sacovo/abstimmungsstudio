from ninja.schema import Schema
from ninja.orm import ModelSchema

from abst.models import Kanton, Vorlage


class Result(Schema):
    final: bool

    ja_stimmen: int
    nein_stimmen: int
    anzahl_stimmberechtigte: int

    stimmbeteiligung: float
    ja_prozent: float


class KantonSchema(ModelSchema):
    class Meta:
        model = Kanton
        fields = ["name", "short", "kanton_id"]


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
    kantonal: bool = False
    region: str = "CH"

    result: dict | None = None


class VorlageListingSchema(ModelSchema):
    class Meta:
        model = Vorlage
        fields = ["name", "vorlagen_id", "finished", "doppeltes_mehr",
                  "angenommen", "ja_staende", "nein_staende", "region", "result"]


class ResultsKantonSchema(Schema):
    kanton: str
    status: str
    anzahl_stimmberechtigte: int
    ja_stimmen: int
    nein_stimmen: int


class ResultsTotalSchema(Schema):
    status: str
    anzahl_stimmberechtigte: int
    ja_stimmen: int
    nein_stimmen: int


class ResultsHistorySchemaGemeinde(Schema):
    time: float
    status: str
    ja_prozent: float
    stimmbeteiligung: float


class ResultsHistorySchemaKanton(Schema):
    time: float
    status: str
    ja_stimmen: int
    nein_stimmen: int
    anzahl_stimmberechtigte: int
    kanton: str


class ResultsHistorySchemaTotal(Schema):
    time: float
    status: str

    ja_stimmen: int
    nein_stimmen: int
    anzahl_stimmberechtigte: int


class ResultsGemeindeSchema(Schema):
    geo_id: int
    status: str

    anzahl_stimmberechtigte: int
    ja_prozent: float
    ja_stimmen: int
    nein_stimmen: int
    stimmbeteiligung: float
