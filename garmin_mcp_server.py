import asyncio
import json
import os
import time
from typing import Any
import httpx
from mcp.server import MCPServer

# Inicializar el servidor MCP (compatible con mcp v2+)
mcp = MCPServer("garmin-local-mcp")

# Ruta al archivo de texto local donde guardas tu Personal API Key (GCDC_...)
API_KEY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".api-key.k"
)
BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_ENDPOINT = (
    "/personalapitoken-service/personal-api/personal-api-key/token"
)

# Caché en memoria para el Bearer Token temporal (válido ~24h)
_token_cache: dict[str, Any] = {
    "access_token": None,
    "expires_at": 0.0,
}


def _read_api_key() -> str:
    """Lee la clave Personal API de Garmin desde el archivo .api-key.k."""
    if not os.path.exists(API_KEY_PATH):
        raise FileNotFoundError(
            f"El archivo de clave no existe en {API_KEY_PATH}"
        )
    with open(API_KEY_PATH, "r", encoding="utf-8") as f:
        api_key = f.read().strip()
    if not api_key:
        raise ValueError("El archivo .api-key.k está vacío.")
    return api_key


async def _get_bearer_token(
    client: httpx.AsyncClient, force_refresh: bool = False
) -> str:
    """Intercambia la Personal API Key (GCDC_...) por un Bearer Token JWT."""
    now = time.time()
    if (
        not force_refresh
        and _token_cache["access_token"]
        and now < _token_cache["expires_at"]
    ):
        return _token_cache["access_token"]

    api_key = _read_api_key()
    response = await client.post(
        TOKEN_ENDPOINT,
        headers={"Authorization": f"ApiKey {api_key}"},
    )
    response.raise_for_status()

    token = response.text.strip().strip('"')
    if not token:
        raise RuntimeError(
            "La API de Garmin devolvió un token vacío al autenticar."
        )

    # Los tokens de Garmin Personal API duran 24h; cacheamos por 23h
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = now + (23 * 3600)
    return token


async def _garmin_get(
    path: str, params: dict[str, Any] | None = None
) -> Any:
    """Realiza una petición GET autenticada a la API oficial de Garmin Connect Personal."""
    clean_params = (
        {k: v for k, v in params.items() if v is not None and v != ""}
        if params
        else None
    )
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        token = await _get_bearer_token(client)
        response = await client.get(
            path,
            params=clean_params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        # Si el token expiró antes de tiempo, refrescar una vez
        if response.status_code == 401:
            token = await _get_bearer_token(client, force_refresh=True)
            response = await client.get(
                path,
                params=clean_params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )

        if response.status_code == 204 or not response.content:
            return {
                "status": 204,
                "message": "Sin datos para el rango o recurso solicitado.",
            }

        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        try:
            return response.json()
        except Exception:
            return response.text


@mcp.tool()
async def list_activities(
    start: int = 0,
    limit: int = 20,
    activityType: str | None = None,
    startDate: str | None = None,
    endDate: str | None = None,
    search: str | None = None,
) -> str:
    """Busca y lista tus entrenamientos/actividades registrados en Garmin Connect.

    Args:
        start: Índice inicial para paginación (por defecto 0).
        limit: Cantidad máxima de actividades a devolver (por defecto 20).
        activityType: Filtro opcional por tipo de actividad (ej. 'running', 'cycling', 'swimming', 'strength_training').
        startDate: Fecha inicial opcional en formato YYYY-MM-DD.
        endDate: Fecha final opcional en formato YYYY-MM-DD.
        search: Texto opcional para buscar por nombre de entrenamiento.
    """
    params = {
        "start": start,
        "limit": limit,
        "activityType": activityType,
        "startDate": startDate,
        "endDate": endDate,
        "search": search,
    }
    data = await _garmin_get(
        "/activitylist-service/activities/search/activities",
        params=params,
    )
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_activity(activityId: int | str) -> str:
    """Obtiene el resumen completo y metadatos detallados de un entrenamiento específico por su activityId

    (incluyendo summaryDTO, zonas, laps/splits, sensores y dispositivos utilizados).

    Args:
        activityId: ID único de la actividad en Garmin Connect.
    """
    data = await _garmin_get(f"/activity-service/activity/{activityId}")
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_activity_details(
    activityId: int | str,
    maxChartSize: int = 200,
    includeGeoPolyline: bool = False,
) -> str:
    """Obtiene las series temporales (muestras punto a punto de frecuencia cardíaca, ritmo/velocidad,

    potencia, cadencia, altitud, etc.) de un entrenamiento específico para análisis profundo del rendimiento.

    Args:
        activityId: ID único de la actividad en Garmin Connect.
        maxChartSize: Cantidad máxima de puntos de serie temporal a muestrear (por defecto 200).
        includeGeoPolyline: Si es True, incluye la polilínea GPS completa (por defecto False para ahorrar contexto).
    """
    data = await _garmin_get(
        f"/activity-service/activity/{activityId}/details",
        params={"maxChartSize": maxChartSize},
    )
    if isinstance(data, dict) and not includeGeoPolyline:
        data.pop("geoPolylineDTO", None)
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_activity_workouts(activityId: int | str) -> str:
    """Obtiene la estructura de pasos/bloques del entrenamiento programado (workout) asociado a una actividad.

    Args:
        activityId: ID único de la actividad en Garmin Connect.
    """
    data = await _garmin_get(
        f"/activity-service/activity/{activityId}/workouts"
    )
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_activity_summary_range(fromDate: str, untilDate: str) -> str:
    """Obtiene el resumen agregado de actividades y entrenamientos en un rango de fechas.

    Args:
        fromDate: Fecha de inicio en formato YYYY-MM-DD.
        untilDate: Fecha de fin en formato YYYY-MM-DD.
    """
    data = await _garmin_get(
        f"/wellnessactivity-service/activity/summary/range/{fromDate}/{untilDate}"
    )
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_training_metrics(startDate: str, endDate: str) -> str:
    """Obtiene tus zonas de frecuencia cardíaca configuradas (heartRateZones/v2) y la evolución

    diaria de VO2 Max / Max MET en un rango de fechas.

    Args:
        startDate: Fecha de inicio para VO2 Max / Max MET en formato YYYY-MM-DD.
        endDate: Fecha de fin para VO2 Max / Max MET en formato YYYY-MM-DD.
    """
    hr_zones, max_met = await asyncio.gather(
        _garmin_get("/biometric-service/heartRateZones/v2"),
        _garmin_get(
            f"/metrics-service/metrics/maxmet/daily/{startDate}/{endDate}"
        ),
        return_exceptions=True,
    )
    result = {
        "heartRateZones": (
            str(hr_zones) if isinstance(hr_zones, Exception) else hr_zones
        ),
        "maxMetDaily": (
            str(max_met) if isinstance(max_met, Exception) else max_met
        ),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_recovery_and_wellness(startDate: str, endDate: str) -> str:
    """Consulta métricas diarias de recuperación y salud (resumen diario, sueño, HRV y Body Battery)

    en un rango de fechas para correlacionar la carga de entrenamiento con tu recuperación.

    Args:
        startDate: Fecha de inicio en formato YYYY-MM-DD.
        endDate: Fecha de fin en formato YYYY-MM-DD.
    """
    date_params = {"startDate": startDate, "endDate": endDate}
    daily_summary, sleep_summary, hrv_daily, body_battery = (
        await asyncio.gather(
            _garmin_get(
                "/usersummary-service/v2/dailysummaries",
                params=date_params,
            ),
            _garmin_get(
                "/sleep-service/sleep/dailySleepSummariesByDate",
                params=date_params,
            ),
            _garmin_get(f"/hrv-service/hrv/daily/{startDate}/{endDate}"),
            _garmin_get(
                "/wellness-service/wellness/bodyBattery/reports/daily",
                params=date_params,
            ),
            return_exceptions=True,
        )
    )
    result = {
        "dailySummaries": (
            str(daily_summary)
            if isinstance(daily_summary, Exception)
            else daily_summary
        ),
        "sleepSummaries": (
            str(sleep_summary)
            if isinstance(sleep_summary, Exception)
            else sleep_summary
        ),
        "hrvDaily": (
            str(hrv_daily) if isinstance(hrv_daily, Exception) else hrv_daily
        ),
        "bodyBatteryDaily": (
            str(body_battery)
            if isinstance(body_battery, Exception)
            else body_battery
        ),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def garmin_api_get(
    endpoint: str, params: dict[str, Any] | None = None
) -> str:
    """Realiza una petición GET directa a cualquier endpoint de la API oficial Garmin Connect Personal

    (https://personal.connectapi.garmin.com).

    Args:
        endpoint: Ruta del endpoint comenzando con '/' (ej. '/biometric-service/heartRateZones/v2').
        params: Diccionario opcional de parámetros query string.
    """
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    data = await _garmin_get(endpoint, params=params)
    return json.dumps(data, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
