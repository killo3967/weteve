"""Registro de herramientas MCP del daemon weteve.

Cada entrada mapea nombre MCP → {description, inputSchema JSON Schema,
handler}. Los handlers son funciones planas sobre el TargetSupervisor y el
TargetStore, serializables a JSON (los agentes solo ven texto/JSON).

Sintaxis de reglas en argumentos MCP: la textual de xTeVe usada en daemon.toml
(``field:pattern:case``), parseada con FilterRule.parse.
"""

import logging
from typing import cast

# Serialización y parseo de reglas viven en la capa de servicio: MCP y REST
# comparten una única implementación.
from src.application.services.daemon.service import (
    TargetService,
)
from src.application.services.daemon.service import (
    base_pattern as _base_pattern,
)
from src.application.services.daemon.service import (
    parse_rules as _parse_rules,
)
from src.application.services.list_manager.models import (
    FilterField,
    FilterRule,
    PipelineTarget,
)

logger = logging.getLogger(__name__)

def build_tool_registry(supervisor, target_store, on_shutdown=None) -> dict:
    """Construye el registro completo de herramientas MCP.

    ``target_store`` es obligatorio para create/update/delete; los handlers
    devuelven {"error": ...} si falta (registro de solo lectura).

    La lógica de dominio vive en ``TargetService``: este registro solo traduce
    JSON-RPC a sus operaciones. Quedan aquí el CRUD de targets y la cartera de
    fuentes, que el servicio aún no cubre.
    """
    service = TargetService(supervisor, target_store)

    # -- estado ---------------------------------------------------------------

    def get_status() -> dict:
        return service.status()

    def shutdown() -> dict:
        if on_shutdown is not None:
            on_shutdown()
        return {"ok": True}

    # -- lectura de targets ------------------------------------------------------

    def list_targets() -> dict:
        return service.list_targets()

    def get_target(target_id: str) -> dict:
        found = service.get_target(target_id)
        if found is None:
            return {"error": f"target {target_id} no existe"}
        return found

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
            # preservar estado derivado de la cartera y del widget de mapeo:
            # update_target solo toca campos escalares
            source_refs=current.source_refs,
            epg_refs=current.epg_refs,
            disabled_channels=current.disabled_channels,
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

    # -- filtros y mapeado (hot-reload si el target está sirviendo) ----------

    def create_filter(target_id: str, field: str, pattern: str,
                      exclude: bool = False, case_sensitive: bool = False,
                      operator: str = "or") -> dict:
        err = _require_store()
        if err:
            return {"error": err}
        found = service.get_target(target_id)
        if found is None:
            return {"error": f"target {target_id} no existe"}
        rules = list(_parse_rules(found["target"]["rules"]))
        if field not in _ALLOWED_FIELDS:
            return {"error": (f"field inválido: {field} "
                              f"(permitidos: {', '.join(_ALLOWED_FIELDS)})")}
        if operator not in ("or", "and"):
            return {"error": f"operator inválido: {operator} (or|and)"}
        if not pattern.strip():
            return {"error": "pattern vacío"}
        # Convención xTeVe usada por el motor (filter_engine) y por la UI:
        # la exclusión se codifica DENTRO del patrón como !{texto}; el campo
        # exclude del dataclass es informativo y el motor lo ignora.
        clean = pattern.strip()
        stored = f"!{{{clean}}}" if exclude else clean
        rule = FilterRule(field=cast(FilterField, field), pattern=stored,
                          exclude=bool(exclude),
                          case_sensitive=bool(case_sensitive),
                          operator=operator)  # type: ignore[arg-type]
        if any(r.field == rule.field and _base_pattern(r.pattern) == clean
               for r in rules):
            return {"error": (f"ya existe una regla {field}:{pattern} "
                              "en el target")}
        rules.append(rule)
        out = service.set_rules(target_id, [r.serialize() for r in rules])
        if "error" in out:
            return out
        return {"ok": True, "rule": rule.serialize(),
                "rules": [r.serialize() for r in rules],
                "reloaded": out["reloaded"]}

    def delete_filter(target_id: str, field: str, pattern: str) -> dict:
        err = _require_store()
        if err:
            return {"error": err}
        found = service.get_target(target_id)
        if found is None:
            return {"error": f"target {target_id} no existe"}
        rules = list(_parse_rules(found["target"]["rules"]))
        remaining = [r for r in rules
                     if not (r.field == field
                             and _base_pattern(r.pattern) == pattern.strip())]
        if len(remaining) == len(rules):
            return {"error": (f"no existe ninguna regla {field}:{pattern} "
                              "en el target")}
        removed = [r.serialize() for r in rules if r not in remaining]
        out = service.set_rules(target_id, [r.serialize() for r in remaining])
        if "error" in out:
            return out
        return {"ok": True, "removed": removed,
                "rules": [r.serialize() for r in remaining],
                "reloaded": out["reloaded"]}

    def modify_mapping(target_id: str, channel_url: str,
                       epg_id: str | None = None) -> dict:
        """Fuerza o elimina (epg_id=None) el mapeo EPG de un canal."""
        err = _require_store()
        if err:
            return {"error": err}
        if not channel_url:
            return {"error": "channel_url vacío"}
        out = service.set_mappings(
            target_id, [{"channel_url": channel_url, "epg_id": epg_id}])
        if "error" in out:
            return out
        if out.get("errors"):
            return {"error": out["errors"][0].get("error", "mapeo rechazado")}
        return {"ok": True, "channel_url": channel_url,
                "epg_id": epg_id or "",
                "epg_overrides": out["target"]["epg_overrides"],
                "reloaded": out["reloaded"]}

    def modify_channels(target_id: str, channels: list[dict]) -> dict:
        """Ficha de canal en lote: dial, nombre mostrado y logo.

        Cada entrada: ``{identity, url?, dial?, display?, logo?}``. Un campo
        ausente no se toca; vacío lo devuelve al automático. Un dial repetido
        no se guarda (devuelve los conflictos).
        """
        if not isinstance(channels, list) or not channels:
            return {"error": "channels debe ser una lista no vacía"}
        out = service.set_channels(target_id, channels)
        if "error" in out:
            return out
        return {"ok": True, "target_id": target_id,
                "fichas": out.get("channels")}

    def modify_mappings(target_id: str,
                        mappings: list[dict]) -> dict:
        """Asignación EPG en lote: [{'channel_url', 'epg_id'|None}, ...].

        Un solo guardado y una sola recarga (a diferencia de N llamadas a
        modify_mapping). epg_id=None elimina el mapeo del canal.
        """
        if not isinstance(mappings, list) or not mappings:
            return {"error": "mappings debe ser una lista no vacía"}
        out = service.set_mappings(target_id, mappings)
        if "error" in out:
            return out
        result: dict = {"ok": True, "applied": out["applied"],
                        "errors": out["errors"],
                        "count": len(out["applied"]),
                        "epg_overrides": out["target"]["epg_overrides"],
                        "reloaded": out["reloaded"]}
        if out.get("partial"):
            result["partial"] = True
        return result

    def start_target(target_id: str) -> dict:
        out = service.start_service(target_id)
        if "error" in out:
            return out
        return {"ok": out["ok"]} if out["ok"] else {
            "ok": False, **supervisor.get_status()["targets"].get(target_id, {}),
        }

    def stop_target(target_id: str) -> dict:
        return {"ok": service.stop_service(target_id)["ok"]}

    def reload_target(target_id: str) -> dict:
        out = service.run(target_id)
        if "error" in out:
            return {"ok": False,
                    **supervisor.get_status().get("targets", {}).get(target_id, {}),
                    "error": out["error"]}
        return {"ok": True}

    # -- pipeline -----------------------------------------------------------------

    def run_pipeline(target_id: str) -> dict:
        # una sola ejecución; la lógica vive en TargetService
        return service.run(target_id)

    def list_tv_channels(target_id: str, limit: int = 50, offset: int = 0,
                         unmatched_only: bool = False) -> dict:
        return service.channels(target_id, limit=int(limit), offset=int(offset),
                                unmatched_only=bool(unmatched_only))

    def list_epg_channels_tool(target_id: str, limit: int = 200,
                               offset: int = 0) -> dict:
        return service.epg_channels(target_id, limit=int(limit),
                                    offset=int(offset))

    def suggest_epg_matches_tool(target_id: str, channel_name: str,
                                 limit: int = 5) -> dict:
        return service.suggest_epg_matches(target_id, channel_name,
                                           limit=int(limit))

    # -- schemas -------------------------------------------------------------------

    STRING = {"type": "string"}
    _ALLOWED_FIELDS = ("group-title", "custom-filter", "tvg-group", "extgrp",
                       "source")
    rules_schema = {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Reglas de filtro en sintaxis xTeVe: 'field:pattern:case'. "
            "field: group-title|custom-filter; {texto}=incluir, !{texto}=excluir"
        ),
    }

    # -- cartera de fuentes ---------------------------------------------------

    def add_source(name: str, kind: str, location: str,
                   refresh_min: int = 0) -> dict:
        return service.add_source(name, kind, location, refresh_min)

    def list_sources(kind: str | None = None) -> dict:
        return service.sources(kind)

    def update_source(id: str, name: str | None = None,
                      location: str | None = None,
                      refresh_min: int | None = None,
                      enabled: bool | None = None) -> dict:
        return service.update_source(id, name=name, location=location,
                                     refresh_min=refresh_min, enabled=enabled)

    def remove_source(id: str) -> dict:
        return service.remove_source(id)

    def set_target_sources(target_id: str, m3u_ids: list[str] | None = None,
                           epg_ids: list[str] | None = None) -> dict:
        if m3u_ids is None and epg_ids is None:
            return {"error": "indica m3u_sources y/o epg_sources"}
        current = service.get_target(target_id)
        if current is None:
            return {"error": f"target {target_id} no existe"}
        target = current["target"]
        m3u = ([r["id"] for r in target["source_refs"]]
               if m3u_ids is None else list(m3u_ids))
        epg = ([r["id"] for r in target["epg_refs"]]
               if epg_ids is None else list(epg_ids))
        out = service.set_target_sources(target_id, m3u, epg)
        if "error" in out:
            return out
        return {"ok": True, "target_id": target_id,
                "m3u_ids": [r["id"] for r in out["target"]["source_refs"]],
                "epg_ids": [r["id"] for r in out["target"]["epg_refs"]]}

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
        "create_filter": {
            "description": ("Añade una regla de filtro a un target y reejecuta "
                            "el pipeline si está sirviendo (hot-reload)"),
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "field": {
                        "type": "string",
                        "enum": list(_ALLOWED_FIELDS),
                        "description": "Campo del canal al que aplica la regla",
                    },
                    "pattern": STRING,
                    "exclude": {
                        "type": "boolean",
                        "description": "true = excluir canales que cumplan",
                    },
                    "case_sensitive": {"type": "boolean"},
                    "operator": {
                        "type": "string",
                        "enum": ["or", "and"],
                        "description": "Cómo se combina con las reglas previas",
                    },
                },
                "required": ["target_id", "field", "pattern"],
            },
            "handler": create_filter,
        },
        "delete_filter": {
            "description": ("Elimina las reglas que coincidan con campo+patrón "
                            "y reejecuta el pipeline si está sirviendo"),
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "field": {"type": "string", "enum": list(_ALLOWED_FIELDS)},
                    "pattern": STRING,
                },
                "required": ["target_id", "field", "pattern"],
            },
            "handler": delete_filter,
        },
        "modify_mapping": {
            "description": ("Fuerza (epg_id) o elimina (epg_id omitido/None) el "
                            "mapeo EPG manual de un canal; hot-reload si sirve"),
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "channel_url": {
                        "type": "string",
                        "description": "URL del canal (clave estable del mapeo)",
                    },
                    "epg_id": {
                        "type": "string",
                        "description": "channel-id del XMLTV; omitir para quitar",
                    },
                },
                "required": ["target_id", "channel_url"],
            },
            "handler": modify_mapping,
        },
        "modify_channels": {
            "description": ("Fija a mano dial, nombre mostrado o logo de uno o "
                            "varios canales (ficha por identidad); hot-reload"),
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "channels": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "identity": {"type": "string"},
                                "url": {"type": "string"},
                                "dial": {"type": ["integer", "null"]},
                                "display": {"type": "string"},
                                "logo": {"type": "string"},
                            },
                            "required": ["identity"],
                        },
                    },
                },
                "required": ["target_id", "channels"],
            },
            "handler": modify_channels,
        },
        "modify_mappings": {
            "description": ("Asignación EPG en lote: aplica varios mapeos "
                            "(epg_id) o eliminaciones (epg_id=null) con UN solo "
                            "guardado y hot-reload final. Devuelve applied/errors"),
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "mappings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "channel_url": {
                                    "type": "string",
                                    "description": "URL del canal M3U",
                                },
                                "epg_id": {
                                    "type": ["string", "null"],
                                    "description": ("channel-id del XMLTV; "
                                                    "null para eliminar el mapeo"),
                                },
                            },
                            "required": ["channel_url"],
                        },
                        "minItems": 1,
                    },
                },
                "required": ["target_id", "mappings"],
            },
            "handler": modify_mappings,
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
            "description": "Arranca el servicio HTTP de un perfil (sirve la lista y la guía)",
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
        "list_tv_channels": {
            "description": ("Canales del target tras filtros (requiere "
                            "reload/run_pipeline previo). Paginado; con "
                            "unmatched_only=true solo los sin EPG asignado"),
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "limit": {"type": "integer", "default": 50,
                               "minimum": 1, "maximum": 500},
                    "offset": {"type": "integer", "default": 0,
                               "minimum": 0},
                    "unmatched_only": {"type": "boolean", "default": False,
                                       "description": ("solo canales sin EPG "
                                                       "asignado")},
                },
                "required": ["target_id"],
            },
            "handler": list_tv_channels,
        },
        "list_epg_channels": {
            "description": ("Catálogo de canales del XMLTV del target "
                            "(id + display_name), paginado. Requiere EPG "
                            "cargado (reload_target/run_pipeline previo)"),
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "limit": {"type": "integer", "default": 200,
                               "minimum": 1, "maximum": 2000},
                    "offset": {"type": "integer", "default": 0,
                               "minimum": 0},
                },
                "required": ["target_id"],
            },
            "handler": list_epg_channels_tool,
        },
        "suggest_epg_matches": {
            "description": ("Candidatos EPG (hasta 5) para un canal M3U por "
                            "similitud de nombre. Solo lectura: asignar con "
                            "modify_mapping/modify_mappings"),
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": STRING,
                    "channel_name": {
                        "type": "string",
                        "description": "Nombre del canal M3U a buscar",
                    },
                    "limit": {"type": "integer", "default": 5,
                               "minimum": 1, "maximum": 20},
                },
                "required": ["target_id", "channel_name"],
            },
            "handler": suggest_epg_matches_tool,
        },
    }
