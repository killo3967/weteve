"""API REST v1 del daemon (solo lecturas por ahora).

Adaptador fino sobre ``TargetService``: no tiene lógica de negocio, solo
traduce rutas y parámetros a llamadas. Se sirve en el mismo puerto que
``/mcp`` (``McpHttpTransport``), bajo el prefijo ``/api/v1``, para no añadir un
tercer puerto al daemon.

Lecturas implementadas:
  GET /api/v1/health
  GET /api/v1/status
  GET /api/v1/targets
  GET /api/v1/targets/{id}
  GET /api/v1/targets/{id}/status
  GET /api/v1/targets/{id}/channels            ?limit&offset&unmatched_only
  GET /api/v1/targets/{id}/mapping-candidates  ?limit&offset
  GET /api/v1/targets/{id}/epg-channels        ?limit&offset
  GET /api/v1/sources
  GET /api/v1/targets/{id}/groups
  GET /api/v1/targets/{id}/group-fields
  POST /api/v1/targets/{id}/preview            {rules: ["group-title:X:0:or"]}

Códigos: 200 ok, 400 parámetro inválido, 404 ruta o target inexistente,
405 método no soportado (las escrituras llegan en la fase siguiente).
"""

import json
from urllib.parse import parse_qs, urlparse

PREFIX = "/api/v1"

_MAX_LIMIT = 1000


def _int_param(query: dict, name: str, default: int,
               maximum: int | None = None) -> tuple[int | None, str | None]:
    raw = (query.get(name) or "").strip()
    if not raw:
        return default, None
    try:
        value = int(raw)
    except ValueError:
        return None, f"{name} debe ser un entero"
    if value < 0:
        return None, f"{name} no puede ser negativo"
    if maximum is not None:
        value = min(value, maximum)
    return value, None


def _bool_param(query: dict, name: str) -> bool:
    return (query.get(name) or "").strip().lower() in ("1", "true", "yes", "si")


def _json_body(body: bytes | None) -> tuple[dict, str | None]:
    """Cuerpo JSON de una escritura: (payload, error). Payload vacío si falla."""
    if not body:
        return {}, None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}, "cuerpo no-JSON"
    if not isinstance(payload, dict):
        return {}, "el cuerpo debe ser un objeto JSON"
    return payload, None


def _status_for(result: dict) -> int:
    """Un cambio rechazado por el servicio vuelve como 400."""
    return 400 if "error" in result else 200


class RestApi:
    """Traduce peticiones HTTP a operaciones de ``TargetService``."""

    def __init__(self, service) -> None:
        self._service = service

    # -- entrada -------------------------------------------------------------

    def handle(self, method: str, raw_path: str,
               body: bytes | None = None) -> tuple[int, dict]:
        parsed = urlparse(raw_path)
        path = parsed.path.rstrip("/") or "/"
        if not path.startswith(PREFIX):
            return 404, {"error": "ruta desconocida"}
        rest = path[len(PREFIX):] or "/"
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}

        if method in ("PUT", "POST"):
            return self._write(method, rest, body)
        if method != "GET":
            return 405, {"error": f"método {method} no soportado"}

        if rest == "/health":
            return 200, {"ok": True, "api": "v1"}
        if rest == "/status":
            return 200, self._service.status()
        if rest == "/targets":
            return 200, self._service.list_targets()
        if rest == "/sources":
            return 200, self._service.sources()

        parts = [p for p in rest.split("/") if p]
        if len(parts) >= 2 and parts[0] == "targets":
            return self._target(method, parts, query)
        return 404, {"error": "ruta desconocida"}

    # -- rutas de target -----------------------------------------------------

    def _target(self, method: str, parts: list[str], query: dict
                ) -> tuple[int, dict]:
        target_id = parts[1]
        if len(parts) == 2:
            found = self._service.get_target(target_id)
            if found is None:
                return 404, {"error": f"target {target_id} no existe"}
            return 200, found
        if len(parts) != 3:
            return 404, {"error": "ruta desconocida"}

        # El resto de rutas necesitan un target existente: distinguir 404 de
        # "no existe" es lo que permite a un frontend borrar la vista.
        if self._service.get_target(target_id) is None:
            return 404, {"error": f"target {target_id} no existe"}

        sub = parts[2]
        if sub == "status":
            return 200, self._service.runtime_status(target_id) or {}

        limit, error = _int_param(query, "limit",
                                  50 if sub == "channels" else 200, _MAX_LIMIT)
        if error:
            return 400, {"error": error}
        offset, error = _int_param(query, "offset", 0)
        if error:
            return 400, {"error": error}

        if sub == "channels":
            return 200, self._service.channels(
                target_id, limit=limit, offset=offset,
                unmatched_only=_bool_param(query, "unmatched_only"))
        if sub == "mapping-candidates":
            return 200, self._service.mapping_candidates(
                target_id, limit=limit, offset=offset)
        if sub == "epg-channels":
            return 200, self._service.epg_channels(
                target_id, limit=limit, offset=offset)
        if sub == "groups":
            return 200, self._service.groups(target_id)
        if sub == "group-fields":
            return 200, self._service.group_fields(target_id)
        return 404, {"error": "ruta desconocida"}

    # -- escrituras ----------------------------------------------------------

    def _write(self, method: str, rest: str,
               body: bytes | None) -> tuple[int, dict]:
        payload, error = _json_body(body)
        if error is not None:
            return 400, {"error": error}
        if rest == "/sources":
            if method != "PUT":
                return 405, {"error": f"método {method} no soportado"}
            if not isinstance(payload.get("sources"), list):
                return 400, {"error": "sources debe ser una lista"}
            result = self._service.set_sources(payload["sources"])
            return _status_for(result), result

        parts = [p for p in rest.split("/") if p]
        if len(parts) != 3 or parts[0] != "targets":
            return 404, {"error": "ruta desconocida"}
        target_id, action = parts[1], parts[2]
        if self._service.get_target(target_id) is None:
            return 404, {"error": f"target {target_id} no existe"}
        reload = bool(payload.pop("reload", True))

        if method == "PUT" and action == "settings":
            result = self._service.update_settings(
                target_id, name=payload.get("name"), port=payload.get("port"),
                bind=payload.get("bind"), reload=reload)
        elif method == "PUT" and action == "rules":
            if not isinstance(payload.get("rules"), list):
                return 400, {"error": "rules debe ser una lista"}
            result = self._service.set_rules(target_id, payload["rules"],
                                            reload=reload)
        elif method == "PUT" and action == "mappings":
            if not isinstance(payload.get("mappings"), list):
                return 400, {"error": "mappings debe ser una lista"}
            result = self._service.set_mappings(target_id, payload["mappings"],
                                               reload=reload)
        elif method == "PUT" and action == "sources":
            if not isinstance(payload.get("m3u_sources"), list) or not isinstance(
                    payload.get("epg_sources"), list):
                return 400, {"error": "m3u_sources y epg_sources deben ser listas"}
            result = self._service.set_target_sources(
                target_id, payload["m3u_sources"], payload["epg_sources"],
                reload=reload)
            return _status_for(result), result
        elif method == "PUT" and action == "disabled":
            if not isinstance(payload.get("disabled_channels"), list):
                return 400, {"error": "disabled_channels debe ser una lista"}
            result = self._service.set_disabled(
                target_id, payload["disabled_channels"], reload=reload)
        elif method == "POST" and action == "preview":
            if not isinstance(payload.get("rules"), list):
                return 400, {"error": "rules debe ser una lista"}
            result = self._service.preview(target_id, payload["rules"])
            return _status_for(result), result
        elif method == "POST" and action == "run":
            result = self._service.run(target_id)
        elif method == "POST" and action == "service":
            choice = str(payload.get("action", "")).lower()
            if choice == "start":
                return 200, self._service.start_service(target_id)
            if choice == "stop":
                return 200, self._service.stop_service(target_id)
            return 400, {"error": "action debe ser 'start' o 'stop'"}
        else:
            return 404, {"error": "ruta desconocida"}
        return _status_for(result), result

    # -- utilidades ----------------------------------------------------------

    @staticmethod
    def dumps(payload: dict) -> bytes:
        """Serialización JSON con acentos legibles."""
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")
