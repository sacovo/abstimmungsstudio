import urllib.parse
import urllib.request

from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseNotFound
from django.shortcuts import get_object_or_404, render

from abst.models import Abstimmungstag, Vorlage

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
        request, "abst/vorlage_map.html", {"vorlage": vorlage, "geo_link": proxy_url}
    )


def vorlage_table_view(request, vorlage_id):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    return render(request, "abst/vorlage_table.html", {"vorlage": vorlage})


def vorlage_compare_view(request, vorlage_id, other_id):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    other = Vorlage.objects.get(vorlagen_id=other_id)
    return render(
        request, "abst/vorlage_compare.html", {"vorlage": vorlage, "other": other}
    )


def proxy_geodata_view(request):
    url = request.GET.get("url")
    if not url:
        return HttpResponseNotFound("URL is required")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            return HttpResponse(
                response.read(), content_type=response.headers.get("Content-Type")
            )
    except Exception as e:
        return HttpResponseNotFound(f"Error fetching URL: {e}")
