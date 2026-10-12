"""Registro de herramientas MCP del daemon weteve.

Cada entrada mapea nombre MCP → {description, inputSchema JSON Schema,
handler}. Los handlers son funciones planas sobre el TargetSupervisor y el
TargetStore, serializables a JSON (los agentes solo ven texto/JSON).

Sintaxis de reglas en argumentos MCP: la textual de xTeVe usada en config.ini
(``field:pattern:case``), parseada con FilterRule.parse.
"""

import logging

from src.application.services.list_manager.models import (
    FilterRule,
    PipelineTarget,
)

logger = logging.getLogger(__name__)

def _serialize_target(t: PipelineTarget) -> dict:
    """PipelineTarget → dict JSON con reglas en sintaxis xteve textual."""
    return {
        "name": t.name,
        "m3u": t.source_index,
        "rules": [r.serialize() for r in t.rules],
        "epg_overrides": dict(t.epg_overrides),
        "port": t.port,
        "starting_number": t.starting_number,
    }

def _parse_rules(raw_rules: list[str]) -> tuple[FilterRule, ...]:
    return tuple(FilterRule.parse(r.strip()) for r in raw_rules if r.strip())

def build_tool_registry(supervisor, target_store, on_shutdown=None) -> dict:
    """Construye el registro completo de herramientas MCP.

    ``target_store`` es obligatorio para create/update/delete; los handlers
    devuelven {"error": ...} si falta (registro de solo lectura).
    """

    # -- estado ---------------------------------------------------------------

    def get_status() -> dict:
        return supervisor.get_status()

    def shutdown() -> dict:
        if on_shutdown is not None:
            on_shutdown()
        return {"ok": True}

    # -- lectura de targets ------------------------------------------------------

    def list_targets() -> dict:
        defs = target_store.list() if target_store else {}
        return {"targets": {tid: _serialize_target(t) for tid, t in defs.items()}}

    def get_target(target_id: str) -> dict:
        t = target_store.get(target_id) if target_store else None
        if t is None:
            return {"error": f"target {target_id} no existe"}
        return {"target": _serialize_target(t)}

    # -- CRUD ---------------------------------------------------------------------

    def _require_store() -> str | None:
        if target_store is None:
            return "configuracion de targets no disponible (modo solo lectura)"
        return None

    def create_target(target_id: str, name: str, m3u: str, port: int,
                      epg: str | None = None, rules: list[str] | None = None,
                      epg_overrides: dict[str, str] | None = None,
                      starting_number: int = 1, bind: str = "127.0.0.1") -> dict:
        err = _require_store()
        if err:
            return {"error": err}
        if target_store.get(target_id) is not None:
            return {"error": f"target {target_id} ya existe"}
        target = PipelineTarget(
            name=name,
            source_index=m3u,
            rules=_parse_rules(rules or []),
            epg_overrides=dict(epg_overrides or {}),
            port=int(port),
            starting_number=int(starting_number),
        )
        target_store.save(target_id, target, epg_path=epg, bind=bind)
        return {"ok": True, "target_id": target_id}

    def update_target(target_id: str, name: str | None = None,
                      m3u: str | None = None, port: int | None = None,
                      epg: str | None = None, rules: list[str] | None = None,
                      epg_overrides: dict[str, str] | None = None,
                      starting_number: int | None = None,
                      bind: str | None = None) -> dict:
        err = _require_store()
        if err:
            return {"error": err}
        current = target_store.get(target_id)
        if current is None:
            return {"error": f"target {target_id} no existe"}
        new = PipelineTarget(
            name=name if name is not None else current.name,
            source_index=m3u if m3u is not None else current.source_index,
            rules=_parse_rules(rules) if rules is not None else current.rules,
            epg_overrides=(dict(epg_overrides) if epg_overrides is not None
                           else current.epg_overrides),
            port=int(port) if port is not None else current.port,
            starting_number=(int(starting_number) if starting_number is not None
                             else current.starting_number),
        )
        target_store.save(target_id, new, epg_path=epg, bind=bind)
        return {"ok": True, "target_id": target_id}

    def delete_target(target_id: str) -> dict:
        err = _require_store()
        if err:
            return {"error": err}
        if not target_store.delete(target_id):
            return {"error": f"target {target_id} no existe"}
        supervisor.remove_target(target_id)
        return {"ok": True}

    # -- ciclo de vida ---------------------------------------------------------

    def _definition(target_id: str):
        """Definición actual de config.ini, no la cacheada al arrancar.

        config.ini lo escribe también la GUI, así que una herramienta de
        arranque/recarga que lea la cache serviría filtros obsoletos. Stores
        antiguos sin reload() siguen usando get().
        """
        if target_store is None:
            return None
        reloader = getattr(target_store, "reload", None)
        if reloader is not None:
            return reloader(target_id)
        return target_store.get(target_id)

    def start_target(target_id: str) -> dict:
        t = _definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        epg = target_store.get_epg_path(target_id) if target_store else None
        ok = supervisor.start_target(target_id, t, epg_path=epg)
        return {"ok": ok} if ok else {
            "ok": False, **supervisor.get_status()["targets"].get(target_id, {}),
        }

    def stop_target(target_id: str) -> dict:
        ok = supervisor.stop_target(target_id)
        return {"ok": ok}

    def reload_target(target_id: str) -> dict:
        t = _definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        epg = target_store.get_epg_path(target_id) if target_store else None
        ok = supervisor.reload_target(target_id, t, epg_path=epg)
        return {"ok": ok} if ok else {
            "ok": False, **supervisor.get_status()["targets"].get(target_id, {}),
        }

    # -- pipeline -----------------------------------------------------------------

    def run_pipeline(target_id: str) -> dict:
        # garantiza definición registrada en supervisor aunque nunca se haya arrancado
        t = _definition(target_id)
        if t is not None:
            epg = target_store.get_epg_path(target_id) if target_store else None
            supervisor.reload_target(target_id, t, epg_path=epg)
        return supervisor.run_pipeline(target_id)

    def preview_channels(target_id: str, limit: int = 20) -> dict:
        t = target_store.get(target_id) if target_store else None
        if t is not None:
            epg = target_store.get_epg_path(target_id) if target_store else None
            supervisor.reload_target(target_id, t, epg_path=epg)
        channels = supervisor.preview_channels(target_id, limit=int(limit))
        return {"channels": channels, "count": len(channels)}

    # -- schemas -------------------------------------------------------------------

    STRING = {"type": "string"}
    rules_schema = {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Reglas de filtro en sintaxis xTeVe: 'field:pattern:case'. "
            "field: group-title|custom-filter; {texto}=incluir, !{texto}=excluir"
        ),
    }

    return {
        "get_status": {
            "description": "Estado del daemon: targets, si sirven, URLs de playlist/EPG, errores",
            "schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "handler": get_status,
        },
        "shutdown": {
            "description": "Detiene el daemon weteve",
            "schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "handler": shutdown,
        },
        "list_targets": {
            "description": "Lista los targets configurados con su definición completa",
            "schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "handler": list_targets,
        },
        "get_target": {
            "description": "Detalle de un target: fuente, reglas, puerto, overrides EPG",
            "schema": {
                "type": "object",
                "properties": {"target_id": STRING},
                "required": ["target_id"],
            },
            "handler": get_target,
        },
        "create_target": {
            "description": "Crea un target: fuente M3U + filtros + EPG + puerto del servicio",
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "name": STRING,
                    "m3u": {"type": "string",
                            "description": "Ruta o URL de la lista M3U"},
                    "epg": {"type": "string",
                            "description": "Ruta/URL del XMLTV (opcional)"},
                    "port": {"type": "integer",
                             "description": "Puerto HTTP local del target (0=efímero)"},
                    "bind": STRING,
                    "rules": rules_schema,
                    "epg_overrides": {
                        "type": "object", "additionalProperties": {"type": "string"},
                        "description": "URL de canal → tvg-id EPG forzado",
                    },
                    "starting_number": {"type": "integer"},
                },
                "required": ["target_id", "name", "m3u", "port"],
            },
            "handler": create_target,
        },
        "update_target": {
            "description": "Modifica campos de un target existente (parcial)",
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "name": STRING,
                    "m3u": {"type": "string"},
                    "epg": {"type": "string"},
                    "port": {"type": "integer"},
                    "bind": STRING,
                    "rules": rules_schema,
                    "epg_overrides": {
                        "type": "object", "additionalProperties": {"type": "string"},
                    },
                    "starting_number": {"type": "integer"},
                },
                "required": ["target_id"],
            },
            "handler": update_target,
        },
        "delete_target": {
            "description": "Elimina un target de la configuración y detiene su servicio",
            "schema": {
                "type": "object",
                "properties": {"target_id": STRING},
                "required": ["target_id"],
            },
            "handler": delete_target,
        },
        "start_target": {
            "description": "Arranca el servicio HTTP de un target (sirve playlist.m3u y epg.xml)",
            "schema": {
                "type": "object",
                "properties": {"target_id": STRING},
                "required": ["target_id"],
            },
            "handler": start_target,
        },
        "stop_target": {
            "description": "Detiene el servicio HTTP de un target",
            "schema": {
                "type": "object",
                "properties": {"target_id": STRING},
                "required": ["target_id"],
            },
            "handler": stop_target,
        },
        "reload_target": {
            "description": "Reejecuta el pipeline del target (relee M3U/EPG y aplica filtros)",
            "schema": {
                "type": "object",
                "properties": {"target_id": STRING},
                "required": ["target_id"],
            },
            "handler": reload_target,
        },
        "run_pipeline": {
            "description": "Ejecuta el pipeline y devuelve métricas: total, filtrados, sin EPG",
            "schema": {
                "type": "object",
                "properties": {"target_id": STRING},
                "required": ["target_id"],
            },
            "handler": run_pipeline,
        },
        "preview_channels": {
            "description": "Primeros N canales filtrados del target (preview sin servir)",
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["target_id"],
            },
            "handler": preview_channels,
        },
    }
