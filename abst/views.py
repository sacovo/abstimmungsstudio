from django.shortcuts import render
from django.http import HttpResponse, HttpResponseNotFound
import urllib.request
import urllib.parse

from abst.models import Abstimmungstag, Vorlage

# Create your views here.


def index_view(request):
    return render(request, "abst/index.html", {})


def abstimmungstag_view(request, date):
    tag = Abstimmungstag.objects.get(date=date)
    return render(request, "abst/abstimmungstag.html", {"tag": tag})


def vorlage_map_view(request, vorlage_id):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    geo_link = vorlage.tag.stand.document.url if vorlage.tag.stand.document else None

    if geo_link:
        proxy_url = f"/proxy-geodata/?url={urllib.parse.quote(geo_link)}"
    else:
        proxy_url = None

    return render(request, "abst/vorlage_map.html", {"vorlage": vorlage, "geo_link": proxy_url})


def vorlage_table_view(request, vorlage_id):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    return render(request, "abst/vorlage_table.html", {"vorlage": vorlage})


def proxy_geodata_view(request):
    url = request.GET.get('url')
    if not url:
        return HttpResponseNotFound("URL is required")

    try:
        req = urllib.request.Request(
            url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            return HttpResponse(response.read(), content_type=response.headers.get('Content-Type'))
    except Exception as e:
        return HttpResponseNotFound(f"Error fetching URL: {e}")
