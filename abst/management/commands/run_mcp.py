import sys
from typing import Literal
import polars as pl
from django.core.management.base import BaseCommand
from mcp.server.fastmcp import FastMCP

from abst.models import Abstimmungstag, Vorlage, Gemeinde, Zaehlkreis
from abst.store import (
    get_abst_result_total,
    get_abst_results,
    get_national_timeline,
    get_correlations,
    get_commune_stats,
    COMMUNE_STATS_METRICS,
)

# Instantiate FastMCP server at module level for easy importability and testing
mcp = FastMCP(
    name="abstimmungsstudio-mcp",
    log_level="INFO"
)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, keys):
        super().__init__(app)
        self.keys = keys
        
    async def dispatch(self, request, call_next):
        # Allow CORS preflight (OPTIONS) requests without key
        if request.method == "OPTIONS":
            return await call_next(request)
            
        # Allow messaging endpoint because it is protected by the session_id
        # generated during the authenticated /sse handshake.
        if request.url.path in ("/messages", "/messages/"):
            return await call_next(request)
            
        # Extract API key
        api_key = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            api_key = auth_header[7:].strip()
        if not api_key:
            api_key = request.headers.get("X-API-Key")
        if not api_key:
            api_key = request.query_params.get("api_key")
            
        if not api_key or api_key not in self.keys:
            return JSONResponse(
                {"error": "Unauthorized: Invalid or missing API key."},
                status_code=401
            )
        return await call_next(request)


# 1. Getting current votes (latest voting day) for CH and all regions
@mcp.tool()
def get_current_votes(region: str = None) -> list[dict]:
    """
    Get the list of votes (vorlagen) for the latest voting day.
    
    :param region: Optional canton short code (e.g. 'ZH', 'BE') or 'CH' to filter by region.
    """
    try:
        latest_tag = Abstimmungstag.objects.order_by("-date").first()
        if not latest_tag:
            return []
        
        vorlagen = Vorlage.objects.filter(tag=latest_tag)
        if region:
            vorlagen = vorlagen.filter(region=region)
        
        res = []
        for v in vorlagen:
            res.append({
                "vorlagen_id": v.vorlagen_id,
                "name": v.name,
                "region": v.region or "CH",
                "finished": v.finished,
                "doppeltes_mehr": v.doppeltes_mehr,
                "angenommen": v.angenommen,
                "ja_staende": v.ja_staende,
                "nein_staende": v.nein_staende,
                "kantonal": v.kantonal,
                "date": str(latest_tag.date),
            })
        return res
    except Exception as e:
        return [{"error": str(e)}]

# 2. Get current result for a vote (counted and projected)
@mcp.tool()
def get_vote_results(vorlage_id: int) -> dict:
    """
    Get the counted and projected results for a specific vote (vorlagen_id).
    """
    try:
        try:
            vorlage = Vorlage.objects.select_related("tag").get(vorlagen_id=vorlage_id)
        except Vorlage.DoesNotExist:
            return {"error": f"Vote with ID {vorlage_id} not found."}
        
        res_total = get_abst_result_total(vorlage_id)
        
        counted_data = None
        projected_data = None
        
        if res_total is not None and len(res_total) > 0:
            for row in res_total.to_dicts():
                ja = row.get("ja_stimmen", 0) or 0
                nein = row.get("nein_stimmen", 0) or 0
                total_votes = ja + nein
                eligible = row.get("anzahl_stimmberechtigte", 0) or 0
                ja_pct = (ja / total_votes * 100.0) if total_votes > 0 else 0.0
                turnout_pct = (total_votes / eligible * 100.0) if eligible > 0 else 0.0
                
                data_dict = {
                    "ja_stimmen": ja,
                    "nein_stimmen": nein,
                    "anzahl_stimmberechtigte": eligible,
                    "ja_prozent": round(ja_pct, 2),
                    "stimmbeteiligung": round(turnout_pct, 2),
                }
                if row["status"] == "final":
                    counted_data = data_dict
                elif row["status"] == "prediction":
                    projected_data = data_dict

        # Fetch timeline (snapshot summaries)
        timeline = get_national_timeline(vorlage_id)
        timeline_latest = timeline[-1] if timeline else None
        
        if timeline_latest:
            if not projected_data:
                projected_data = {}
            projected_data.update({
                "ja_prozent": round(timeline_latest.get("projected_yes_prozent", 0.0), 2),
                "stimmbeteiligung": round(timeline_latest.get("projected_stimmbeteiligung", 0.0), 2),
            })
            if not counted_data:
                counted_data = {}
            counted_data.update({
                "ja_prozent": round(timeline_latest.get("counted_yes_prozent", 0.0), 2),
                "stimmbeteiligung": round(timeline_latest.get("counted_stimmbeteiligung", 0.0), 2),
            })
            
        response = {
            "vorlage_id": vorlage.vorlagen_id,
            "name": vorlage.name,
            "region": vorlage.region or "CH",
            "date": str(vorlage.tag.date),
            "finished": vorlage.finished,
            "angenommen": vorlage.angenommen,
            "counted": counted_data,
            "projected": projected_data,
        }
        
        if timeline_latest:
            response["confidence_intervals"] = {
                "ci_10": round(timeline_latest.get("ci_10", 0.0), 2),
                "ci_25": round(timeline_latest.get("ci_25", 0.0), 2),
                "ci_75": round(timeline_latest.get("ci_75", 0.0), 2),
                "ci_90": round(timeline_latest.get("ci_90", 0.0), 2),
                "mae": round(timeline_latest.get("mae", 0.0), 2),
            }
        return response
    except Exception as e:
        return {"error": str(e)}

# 3. Perform correlation analysis
@mcp.tool()
def perform_correlation_analysis(vorlage_id: int, selected_metric: str = "ja_prozent") -> list[dict]:
    """
    Perform Pearson correlation analysis between vote outcomes and municipality statistics.
    
    :param vorlage_id: The ID of the vote to analyze.
    :param selected_metric: The metric to correlate against (e.g. 'ja_prozent', 'stimmbeteiligung'). Default is 'ja_prozent'.
    """
    try:
        return get_correlations(vorlage_id, selected_metric)
    except Exception as e:
        return [{"error": str(e)}]

# 4. For one specific or all communes of a vote get one or multiple columns of the commune stats
@mcp.tool()
def get_commune_statistics(vorlage_id: int, columns: list[str], geo_id: int = None) -> list[dict]:
    """
    Get demographic/socioeconomic/geographic statistics for one or all communes of a vote.
    
    Available columns include:
    - Population: 'pop_total_2024', 'pop_foreign_ratio_pct_2024', 'pop_change_pct_10yr'
    - Age groups: 'age_bucket_0_17_ratio_pct_2024', 'age_bucket_18_29_ratio_pct_2024', 'age_bucket_30_49_ratio_pct_2024', 'age_bucket_50_64_ratio_pct_2024', 'age_bucket_65_plus_ratio_pct_2024'
    - Civil status: 'pop_marital_single_ratio_pct_2024', 'pop_marital_married_ratio_pct_2024', 'pop_marital_divorced_ratio_pct_2024'
    - Area: 'area_settlement_ratio_pct', 'area_agriculture_ratio_pct', 'area_forest_ratio_pct'
    - Economy: 'businesses_total_2023', 'businesses_density_per_1000_2023', 'employees_total_2023', 'employees_density_per_1000_2023'
    - Tourism: 'tourism_arrivals_2024', 'tourism_nights_2024'
    
    :param vorlage_id: The ID of the vote to define the list of communes.
    :param columns: List of statistic keys to retrieve.
    :param geo_id: Optional. Return data only for this specific commune ID.
    """
    try:
        try:
            vorlage = Vorlage.objects.select_related("tag__stand").get(vorlagen_id=vorlage_id)
        except Vorlage.DoesNotExist:
            return [{"error": f"Vote with ID {vorlage_id} not found."}]
        
        # Fetch communes
        communes = Gemeinde.objects.filter(stand=vorlage.tag.stand)
        if geo_id is not None:
            communes = communes.filter(geo_id=geo_id)
        
        commune_info = {
            c["geo_id"]: {"name": c["name"], "kanton": c["kanton"]}
            for c in communes.values("geo_id", "name", "kanton")
        }
        
        if not commune_info:
            return []
        
        df = get_commune_stats(columns)
        if df is None or df.is_empty():
            return []
        
        # Filter to only the communes on this GeoStand
        df = df.filter(pl.col("geo_id").is_in(list(commune_info.keys())))
        
        results = []
        for row in df.to_dicts():
            gid = row["geo_id"]
            info = commune_info.get(gid, {"name": "Unknown", "kanton": "Unknown"})
            item = {
                "geo_id": gid,
                "name": info["name"],
                "kanton": info["kanton"],
            }
            for col in columns:
                item[col] = row.get(col)
            results.append(item)
        
        return results
    except Exception as e:
        return [{"error": str(e)}]

# 5. Get the per commune result for a vote (turnout and yes percentage and population allowed to vote)
@mcp.tool()
def get_commune_results_for_vote(vorlage_id: int, geo_id: int = None) -> list[dict]:
    """
    Get the per-commune results (turnout, yes percentage, and eligible voters) for a vote.
    
    :param vorlage_id: The ID of the vote.
    :param geo_id: Optional. Return data only for this specific commune/counting district ID.
    """
    try:
        try:
            vorlage = Vorlage.objects.select_related("tag__stand").get(vorlagen_id=vorlage_id)
        except Vorlage.DoesNotExist:
            return [{"error": f"Vote with ID {vorlage_id} not found."}]
        
        df_results = get_abst_results(vorlage_id)
        if df_results is None or df_results.is_empty():
            return []
        
        if geo_id is not None:
            df_results = df_results.filter(pl.col("geo_id") == geo_id)
        
        # Load names/cantons
        communes = Gemeinde.objects.filter(stand=vorlage.tag.stand)
        if geo_id is not None:
            communes = communes.filter(geo_id=geo_id)
        commune_info = {
            c["geo_id"]: {"name": c["name"], "kanton": c["kanton"]}
            for c in communes.values("geo_id", "name", "kanton")
        }
        
        zk_info = {}
        if vorlage.has_zk:
            zaehlkreise = Zaehlkreis.objects.filter(gemeinde__stand=vorlage.tag.stand).select_related("gemeinde")
            if geo_id is not None:
                zaehlkreise = zaehlkreise.filter(geo_id=geo_id)
            zk_info = {
                z.geo_id: {"name": z.name, "kanton": z.gemeinde.kanton}
                for z in zaehlkreise
            }
        
        results = []
        for row in df_results.to_dicts():
            gid = row["geo_id"]
            info = commune_info.get(gid) or zk_info.get(gid) or {"name": "Unknown", "kanton": "Unknown"}
            
            results.append({
                "geo_id": gid,
                "name": info["name"],
                "kanton": info["kanton"],
                "status": row["status"],
                "eligible_voters": row["anzahl_stimmberechtigte"],
                "yes_stimmen": row["ja_stimmen"],
                "nein_stimmen": row["nein_stimmen"],
                "yes_pct": round(row["ja_prozent"], 2) if row["ja_prozent"] is not None else None,
                "turnout_pct": round(row["stimmbeteiligung"], 2) if row["stimmbeteiligung"] is not None else None,
            })
        return results
    except Exception as e:
        return [{"error": str(e)}]

# 6. Perform Wählerwanderung for a vote or national election and party/partygroup/voting block (result as matrix)
@mcp.tool()
def perform_waehlerwanderung(
    vorlage_id: int,
    source_type: Literal["vote", "election"],
    source_id: int = None,
    wahlen_scope: Literal["partei", "parteigruppe", "lager"] = "partei"
) -> dict:
    """
    Perform Wählerwanderung (voter transition analysis) from a source election/vote to a target vote.
    
    :param vorlage_id: Target vote ID (vorlagen_id) we are analyzing transitions to.
    :param source_type: 'vote' to analyze transitions from a previous vote, or 'election' to analyze transitions from the 2023 National Council election.
    :param source_id: The source vote ID (vorlagen_id) if source_type is 'vote'.
    :param wahlen_scope: Election grouping level if source_type is 'election': 'partei' (party), 'parteigruppe' (party group), or 'lager' (voting block). Default is 'partei'.
    """
    if source_type not in ("vote", "election"):
        return {"error": "source_type must be either 'vote' or 'election'."}
    if source_type == "vote" and not source_id:
        return {"error": "source_id is required when source_type is 'vote'."}
    if wahlen_scope not in ("partei", "parteigruppe", "lager"):
        return {"error": "wahlen_scope must be one of 'partei', 'parteigruppe', 'lager'."}
    
    from abst.behavior import calculate_behavior
    try:
        res = calculate_behavior(
            target_id=vorlage_id,
            source_type=source_type,
            source_id=source_id,
            wahlen_scope=wahlen_scope
        )
        return res
    except Exception as e:
        return {"error": str(e)}

# 7. Get voter transition options for a vote
@mcp.tool()
def get_voter_transition_options(vorlage_id: int) -> list[dict]:
    """
    Get the list of available sources (previous votes or elections) for transition analysis.
    
    :param vorlage_id: Target vote ID.
    """
    from abst.behavior import get_behavior_options
    try:
        return get_behavior_options(vorlage_id)
    except Exception as e:
        return [{"error": str(e)}]


class Command(BaseCommand):
    help = "Runs the Model Context Protocol (MCP) server for the Abstimmungsstudio application."

    def add_arguments(self, parser):
        parser.add_argument(
            "--transport",
            choices=["stdio", "sse"],
            default="stdio",
            help="Transport type to run the MCP server on (default: stdio)",
        )
        parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Host to bind the SSE transport server on (default: 127.0.0.1)",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8000,
            help="Port to run the SSE transport on (default: 8000)",
        )

    def handle(self, *args, **options):
        import os
        transport = options["transport"]
        host = options["host"]
        port = options["port"]

        # Override FastMCP settings from command line options
        mcp.settings.host = host
        mcp.settings.port = port

        # If binding to a non-local host, disable DNS rebinding protection so reverse proxies can route requests
        if host not in ("127.0.0.1", "localhost", "::1"):
            mcp.settings.transport_security = None

        # Start the MCP server using FastMCP's built-in run method
        if transport == "stdio":
            # In stdio transport, check if API key is in environment, but warn that it won't be checked per-request
            allowed_keys_str = os.environ.get("MCP_API_KEYS") or os.environ.get("API_KEY") or ""
            allowed_keys = [k.strip() for k in allowed_keys_str.split(",") if k.strip()]
            if allowed_keys:
                sys.stderr.write("WARNING: MCP_API_KEYS is configured, but API key verification is not enforced for stdio transport as it is inherently secure via local process boundaries.\n")
            
            mcp.run(transport="stdio")
            
        elif transport == "sse":
            import uvicorn
            import anyio
            
            starlette_app = mcp.sse_app()
            
            # Check for API keys in environment
            allowed_keys_str = os.environ.get("MCP_API_KEYS") or os.environ.get("API_KEY") or ""
            allowed_keys = [k.strip() for k in allowed_keys_str.split(",") if k.strip()]
            
            if allowed_keys:
                # Wrap the app with middleware
                starlette_app.add_middleware(APIKeyMiddleware, keys=allowed_keys)
                self.stdout.write(self.style.WARNING(f"MCP SSE server starting with API Key authentication (loaded {len(allowed_keys)} keys)."))
            else:
                self.stdout.write(self.style.WARNING("MCP SSE server starting without API Key authentication. Define MCP_API_KEYS in your environment to enable it."))
                
            config = uvicorn.Config(
                starlette_app,
                host=host,
                port=port,
                log_level=mcp.settings.log_level.lower(),
            )
            server = uvicorn.Server(config)
            
            # Run server inside anyio event loop as done by FastMCP
            anyio.run(server.serve)
