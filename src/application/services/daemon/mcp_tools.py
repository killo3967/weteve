"""Registro de herramientas MCP del daemon weteve.

Cada entrada mapea nombre MCP → {description, inputSchema JSON Schema,
handler}. Los handlers son funciones planas sobre el TargetSupervisor y el
TargetStore, serializables a JSON (los agentes solo ven texto/JSON).

Sintaxis de reglas en argumentos MCP: la textual de xTeVe usada en config.ini
(``field:pattern:case``), parseada con FilterRule.parse.
"""

import logging
from pathlib import Path

from src.application.services.list_manager.models import (
    FilterRule,
    PipelineTarget,
)
from src.infrastructure.client.sources_store import load_catalog, save_catalog

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
        "source_refs": [{"id": r.id, "name": r.name, "location": r.location}
                        for r in t.source_refs],
        "epg_refs": [{"id": r.id, "name": r.name, "location": r.location}
                     for r in t.epg_refs],
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

    # -- cartera de fuentes ---------------------------------------------------

    def _catalog_path() -> Path | None:
        if target_store is None:
            return None
        return Path(getattr(target_store, "_path"))

    def add_source(name: str, kind: str, location: str,
                   refresh_min: int = 0) -> dict:
        path = _catalog_path()
        if path is None:
            return {"error": "configuracion de targets no disponible"}
        if kind not in ("m3u", "epg"):
            return {"error": f"kind inválido: {kind} (m3u|epg)"}
        catalog = load_catalog(path)
        entry = catalog.add_new(name, kind, "url" if location.lower().startswith(("http://", "https://")) else "file", location, refresh_min)
        save_catalog(path, catalog)
        return {"id": entry.id, "name": entry.name, "kind": entry.kind,
                "location": entry.location, "refresh_min": entry.refresh_min}

    def list_sources(kind: str | None = None) -> dict:
        path = _catalog_path()
        if path is None:
            return {"error": "configuracion de targets no disponible"}
        catalog = load_catalog(path)
        entries = catalog.all() if not kind else catalog.of_kind(kind)
        return {"sources": [
            {"id": e.id, "name": e.name, "kind": e.kind,
             "source_type": e.source_type, "location": e.location,
             "refresh_min": e.refresh_min, "enabled": e.enabled}
            for e in entries
        ]}

    def update_source(id: str, name: str | None = None,
                      location: str | None = None,
                      refresh_min: int | None = None) -> dict:
        path = _catalog_path()
        if path is None:
            return {"error": "configuracion de targets no disponible"}
        catalog = load_catalog(path)
        entry = catalog.get(id)
        if entry is None:
            return {"error": f"fuente {id} no existe"}
        updates = {}
        if name is not None:
            updates["name"] = name
        if location is not None:
            updates["location"] = location
            updates["source_type"] = ("url" if location.lower().startswith(("http://", "https://")) else "file")
        if refresh_min is not None:
            updates["refresh_min"] = max(0, int(refresh_min))
        updated = catalog.update(id, **updates)
        save_catalog(path, catalog)
        return {"ok": True, "source": {
            "id": updated.id, "name": updated.name, "kind": updated.kind,
            "source_type": updated.source_type, "location": updated.location,
            "refresh_min": updated.refresh_min, "enabled": updated.enabled}}

    def remove_source(id: str) -> dict:
        path = _catalog_path()
        if path is None:
            return {"error": "configuracion de targets no disponible"}
        catalog = load_catalog(path)
        if catalog.get(id) is None:
            return {"error": f"fuente {id} no existe"}
        catalog.remove(id)
        save_catalog(path, catalog)
        return {"ok": True}

    def set_target_sources(target_id: str, m3u_ids: list[str] | None = None,
                           epg_ids: list[str] | None = None) -> dict:
        path = _catalog_path()
        if path is None:
            return {"error": "configuracion de targets no disponible"}
        catalog = load_catalog(path)
        for sid in (m3u_ids or []) + (epg_ids or []):
            if catalog.get(sid) is None:
                return {"error": f"fuente {sid} no existe"}
        t = target_store.reload(target_id) if hasattr(target_store, "reload") \
            else target_store.get(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        from dataclasses import replace

        from src.application.services.list_manager.models import SourceRef

        def _refs(ids: list[str]) -> tuple:
            return tuple(
                SourceRef(e.id, e.name, e.location)
                for e in (catalog.get(sid) for sid in ids) if e
            )

        if m3u_ids is not None:
            t = replace(t, source_refs=_refs(m3u_ids))
        if epg_ids is not None:
            t = replace(t, epg_refs=_refs(epg_ids))
        target_store.save(target_id, t,
                          epg_path=target_store.get_epg_path(target_id))
        return {"ok": True, "target_id": target_id,
                "m3u_ids": [r.id for r in t.source_refs],
                "epg_ids": [r.id for r in t.epg_refs]}

    return {
        "get_status": {
            "description": "Estado del daemon: targets, si sirven, URLs de playlist/EPG, errores",
            "schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "handler": get_status,
        },
        "add_source": {
            "description": "Añade una fuente a la cartera global (kind: m3u|epg)",
            "schema": {
                "type": "object",
                "properties": {
                    "name": STRING,
                    "kind": {"type": "string", "enum": ["m3u", "epg"]},
                    "location": {"type": "string",
                                 "description": "URL http(s) o ruta de fichero"},
                    "refresh_min": {"type": "integer", "minimum": 0,
                                    "description": "Minutos entre recargas; 0 = estática"},
                },
                "required": ["name", "kind", "location"],
            },
            "handler": add_source,
        },
        "list_sources": {
            "description": "Lista la cartera de fuentes (filtro opcional por kind)",
            "schema": {
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": ["m3u", "epg"]}},
            },
            "handler": list_sources,
        },
        "update_source": {
            "description": "Actualiza nombre/ubicación/refresco de una fuente",
            "schema": {
                "type": "object",
                "properties": {
                    "id": STRING,
                    "name": STRING,
                    "location": STRING,
                    "refresh_min": {"type": "integer", "minimum": 0},
                },
                "required": ["id"],
            },
            "handler": update_source,
        },
        "remove_source": {
            "description": "Elimina una fuente de la cartera",
            "schema": {
                "type": "object",
                "properties": {"id": STRING},
                "required": ["id"],
            },
            "handler": remove_source,
        },
        "set_target_sources": {
            "description": "Selecciona qué fuentes de la cartera usa un target (unión)",
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "m3u_ids": {"type": "array", "items": {"type": "string"}},
                    "epg_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["target_id"],
            },
            "handler": set_target_sources,
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
