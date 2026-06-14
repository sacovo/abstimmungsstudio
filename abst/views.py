import urllib.parse
import urllib.request
import json

from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseNotFound
from django.shortcuts import get_object_or_404, render

from abst.models import Abstimmungstag, PredictionEvaluationReport, Vorlage, Gemeinde, Zaehlkreis

# Create your views here.


def index_view(request):
    return render(request, "abst/index.html", {})


def abstimmungstag_view(request, date=None):
    if date:
        try:
            tag = get_object_or_404(Abstimmungstag, date=date)
        except ValidationError:
            return HttpResponseNotFound("Invalid date format. Use YYYY-MM-DD.")
    else:
        tag = Abstimmungstag.objects.order_by("-date").first()
    return render(request, "abst/abstimmungstag.html", {"tag": tag})


def vorlage_map_view(request, vorlage_id):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    geo_link = vorlage.tag.stand.document.url if vorlage.tag.stand.document else None

    if geo_link:
        proxy_url = f"/proxy-geodata/?url={urllib.parse.quote(geo_link)}"
    else:
        proxy_url = None

    return render(
        request, "abst/vorlage_map.html", {
            "vorlage": vorlage, "geo_link": proxy_url}
    )


def vorlage_table_view(request, vorlage_id):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    return render(request, "abst/vorlage_table.html", {"vorlage": vorlage})


def vorlage_scatterplot_view(request, vorlage_id):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    return render(request, "abst/vorlage_scatterplot.html", {"vorlage": vorlage})


def vorlage_compare_view(request, vorlage_id, other_id):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    other = Vorlage.objects.get(vorlagen_id=other_id)
    return render(
        request, "abst/vorlage_compare.html", {
            "vorlage": vorlage, "other": other}
    )


def wahlen_map_view(request):
    latest_tag = Abstimmungstag.objects.order_by("-date").first()
    geo_link = None

    if latest_tag and latest_tag.stand and latest_tag.stand.document:
        geo_link = latest_tag.stand.document.url

    if geo_link:
        proxy_url = f"/proxy-geodata/?url={urllib.parse.quote(geo_link)}"
    else:
        proxy_url = None

    return render(request, "abst/wahlen_map.html", {"geo_link": proxy_url})


def proxy_geodata_view(request):
    url = request.GET.get("url")
    if not url:
        return HttpResponseNotFound("URL is required")

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            return HttpResponse(
                response.read(), content_type=response.headers.get("Content-Type")
            )
    except Exception as e:
        return HttpResponseNotFound(f"Error fetching URL: {e}")


from django.contrib.auth.decorators import login_required

@login_required
def vorlage_behavior_view(request, vorlage_id):
    vorlage = get_object_or_404(Vorlage, vorlagen_id=vorlage_id)
    return render(request, "abst/vorlage_behavior.html", {"vorlage": vorlage})


def evaluation_view(request):
    reports = PredictionEvaluationReport.objects.order_by("-created_at")
    latest_reports_dict = {}
    for r in reports:
        if r.region not in latest_reports_dict:
            latest_reports_dict[r.region] = r

    latest_reports = list(latest_reports_dict.values())
    latest_reports.sort(key=lambda r: (r.region != "CH", r.region))

    selected_region = request.GET.get("region", "CH")
    active_report = latest_reports_dict.get(selected_region)
    if not active_report and latest_reports:
        active_report = latest_reports[0]
        selected_region = active_report.region

    # Get all vote names for display
    evaluated_votes = []
    if active_report:
        for v_id in active_report.vote_ids:
            try:
                v = Vorlage.objects.get(vorlagen_id=v_id)
                evaluated_votes.append({
                    "id": v_id,
                    "name": v.name
                })
            except Vorlage.DoesNotExist:
                evaluated_votes.append({
                    "id": v_id,
                    "name": f"Vorlage ID {v_id}"
                })

    context = {
        "reports": latest_reports,
        "active_report": active_report,
        "selected_region": selected_region,
        "evaluated_votes": evaluated_votes,
    }
    return render(request, "abst/evaluation.html", context)


@login_required
def manual_entry_view(request):
    latest_tag = Abstimmungstag.objects.order_by("-date").first()
    if not latest_tag:
        return render(request, "abst/manual_entry.html", {"error": "Keine Abstimmungstage vorhanden."})

    vorlagen = Vorlage.objects.filter(tag=latest_tag, region="CH").order_by("name")

    # Fetch Gemeinden and Zaehlkreise for the latest tag's geostand
    gemeinden = list(Gemeinde.objects.filter(stand=latest_tag.stand).values("name", "kanton", "geo_id").order_by("name"))
    zaehlkreise = list(Zaehlkreis.objects.filter(gemeinde__stand=latest_tag.stand).values("name", "gemeinde__kanton", "geo_id").order_by("name"))

    locations = []
    for g in gemeinden:
        locations.append({
            "name": g["name"],
            "kanton": g["kanton"],
            "geo_id": g["geo_id"],
            "type": "Gemeinde"
        })
    for z in zaehlkreise:
        locations.append({
            "name": z["name"],
            "kanton": z["gemeinde__kanton"],
            "geo_id": z["geo_id"],
            "type": "Zählkreis"
        })

    context = {
        "tag": latest_tag,
        "vorlagen": vorlagen,
        "locations_json": json.dumps(locations),
    }
    return render(request, "abst/manual_entry.html", context)

