"""TargetService: operaciones de dominio sobre los targets.

Única capa con lógica entre el supervisor/pipeline y los transportes. La API
REST y el registro de herramientas MCP son adaptadores finos: traducen
HTTP/JSON-RPC a llamadas de esta clase, sin reglas de negocio propias.

Las lecturas salen del snapshot que mantiene el supervisor (no reejecutan el
pipeline). Solo ``run`` ejecuta.
"""

import logging
from dataclasses import replace
from typing import cast

from src.application.services.list_manager.models import (
    FilterRule,
    PipelineTarget,
    SourceRef,
)
from src.application.services.list_manager.source_catalog import (
    SourceCatalog,
    SourceEntry,
    SourceKind,
    SourceType,
    new_source_id,
)

logger = logging.getLogger(__name__)


def serialize_target(t: PipelineTarget) -> dict:
    """PipelineTarget → dict JSON con reglas en sintaxis xteve textual."""
    return {
        "name": t.name,
        "m3u": t.source_index,
        "rules": [r.serialize() for r in t.rules],
        "epg_overrides": dict(t.epg_overrides),
        "disabled_channels": list(t.disabled_channels),
        "port": t.port,
        "starting_number": t.starting_number,
        "source_refs": [{"id": r.id, "name": r.name, "location": r.location}
                        for r in t.source_refs],
        "epg_refs": [{"id": r.id, "name": r.name, "location": r.location}
                     for r in t.epg_refs],
    }


def serialize_source(entry: SourceEntry) -> dict:
    """SourceEntry → dict JSON para la API (y para la UI)."""
    return {
        "id": entry.id,
        "name": entry.name,
        "kind": entry.kind,
        "source_type": entry.source_type,
        "location": entry.location,
        "refresh_min": entry.refresh_min,
        "enabled": entry.enabled,
    }


def base_pattern(stored: str) -> str:
    """Patrón sin la envoltura xTeVe: '!{texto}' → 'texto'."""
    s = stored.strip()
    if s.startswith("!") and s.endswith("}") and "{" in s:
        return s[2:-1] if s[1] == "{" else s[1:-1]
    return s


def parse_rules(raw_rules: list[str]) -> tuple[FilterRule, ...]:
    return tuple(FilterRule.parse(r.strip()) for r in raw_rules if r.strip())


class TargetService:
    """Fachada de operaciones sobre targets para REST y MCP."""

    def __init__(self, supervisor, store) -> None:
        self._supervisor = supervisor
        self._store = store

    # -- lectura -------------------------------------------------------------

    def _definition(self, target_id: str) -> PipelineTarget | None:
        """Definición desde disco cuando se pueda (config.ini lo editan otros).

        ``TargetStore.reload`` reescribe la cache del store; es el mismo
        criterio que usaban las herramientas MCP para no servir lo que había
        al arrancar.
        """
        if self._store is None:
            return None
        reload = getattr(self._store, "reload", None)
        if reload is not None:
            return reload(target_id)
        return self._store.get(target_id)

    def status(self) -> dict:
        """Estado agregado de los targets (running, URLs, errores)."""
        return self._supervisor.get_status()

    def list_targets(self) -> dict:
        targets = self._store.list() if self._store is not None else {}
        return {"targets": {tid: serialize_target(t)
                            for tid, t in targets.items()}}

    def get_target(self, target_id: str) -> dict | None:
        t = self._definition(target_id)
        if t is None:
            return None
        return {"target": serialize_target(t)}

    def runtime_status(self, target_id: str) -> dict | None:
        """Estado de ejecución de un target, o None si no existe."""
        targets = self._supervisor.get_status().get("targets", {})
        return targets.get(target_id)

    def channels(self, target_id: str, limit: int = 50, offset: int = 0,
                 unmatched_only: bool = False) -> dict:
        """Canales servidos; la primera lectura rellena el snapshot.

        Si el target nunca se ejecutó se ejecuta una vez para poder responder;
        las siguientes lecturas salen del snapshot (nunca reejecutan).
        """
        if not self._supervisor.has_snapshot(target_id):
            t = self._definition(target_id)
            if t is not None:
                epg_path = (self._store.get_epg_path(target_id)
                            if self._store is not None else None)
                self._supervisor.reload_target(target_id, t, epg_path=epg_path)
        return self._supervisor.list_tv_channels(
            target_id, limit=int(limit), offset=int(offset),
            unmatched_only=bool(unmatched_only))

    def mapping_candidates(self, target_id: str, limit: int = 200,
                           offset: int = 0,
                           include_served: bool = False) -> dict:
        return self._supervisor.mapping_candidates(
            target_id, limit=int(limit), offset=int(offset),
            include_served=bool(include_served))

    def epg_channels(self, target_id: str, limit: int = 200,
                     offset: int = 0) -> dict:
        return self._supervisor.list_epg_channels(
            target_id, limit=int(limit), offset=int(offset))

    def groups(self, target_id: str) -> dict:
        return {"groups": self._supervisor.groups(target_id)}

    def group_fields(self, target_id: str) -> dict:
        return {"fields": self._supervisor.group_fields(target_id)}

    def preview(self, target_id: str, rules: list[str]) -> dict:
        """Vista previa de canales con unas reglas (no persiste nada)."""
        if not isinstance(rules, (list, tuple)):
            return {"error": "rules debe ser una lista"}
        parsed = parse_rules(list(rules))
        return self._supervisor.preview_filter(target_id, parsed)

    def suggest_epg_matches(self, target_id: str, channel_name: str,
                            limit: int = 5) -> dict:
        return self._supervisor.suggest_epg_matches(
            target_id, channel_name=str(channel_name), limit=int(limit))

    # -- cartera de fuentes --------------------------------------------------

    @property
    def _config_path(self):
        return getattr(self._store, "path", None) if self._store is not None else None

    def _catalog(self) -> SourceCatalog:
        if self._config_path is None:
            return SourceCatalog()
        from src.infrastructure.client.sources_store import load_catalog
        return load_catalog(self._config_path)

    def _save_catalog(self, catalog: SourceCatalog) -> None:
        if self._config_path is None:
            raise RuntimeError("config.ini no disponible")
        from src.infrastructure.client.sources_store import save_catalog
        save_catalog(self._config_path, catalog)

    @staticmethod
    def _validate_location(location: str) -> str | None:
        """Error si la ubicación no es usable (el fallo típico: ``http://``)."""
        loc = (location or "").strip()
        if not loc:
            return "location vacía"
        if loc.lower().startswith(("http://", "https://")):
            from urllib.parse import urlparse

            if not urlparse(loc).netloc:
                return f"location inválida (sin host): {loc}"
        return None

    def _source_entry(self, raw: dict) -> tuple[SourceEntry | None, str | None]:
        """Valida y normaliza una entrada de cartera (API y MCP)."""
        raw = raw or {}
        kind = str(raw.get("kind", "")).strip().lower()
        location = str(raw.get("location", "")).strip()
        if kind not in ("m3u", "epg"):
            return None, f"kind inválido: {kind or '(vacío)'} (m3u|epg)"
        kind = cast(SourceKind, kind)
        error = self._validate_location(location)
        if error:
            return None, error
        source_type = str(raw.get("source_type") or "").strip().lower()
        if source_type not in ("file", "url"):
            source_type = ("url" if location.lower().startswith(
                ("http://", "https://")) else "file")
        source_type = cast(SourceType, source_type)
        try:
            refresh = max(0, int(raw.get("refresh_min") or 0))
        except (TypeError, ValueError):
            refresh = 0
        return SourceEntry(
            id=str(raw.get("id") or new_source_id()),
            name=str(raw.get("name") or location),
            kind=kind,
            source_type=source_type,
            location=location,
            refresh_min=refresh,
            enabled=bool(raw.get("enabled", True)),
        ), None

    def sources(self, kind: str | None = None) -> dict:
        entries = self._catalog().all()
        if kind:
            entries = [e for e in entries if e.kind == kind]
        return {"sources": [serialize_source(e) for e in entries]}

    def set_sources(self, sources: list[dict]) -> dict:
        """Reemplaza la cartera completa; los ids que llegan se conservan."""
        if self._config_path is None:
            return {"error": "config.ini no disponible"}
        if not isinstance(sources, list):
            return {"error": "sources debe ser una lista"}
        entries: list[SourceEntry] = []
        errors: list[dict] = []
        for raw in sources:
            entry, error = self._source_entry(raw)
            if error is not None:
                errors.append({"error": error, "source": raw})
                continue
            assert entry is not None  # _source_entry devuelve uno u otro
            entries.append(entry)
        if errors:
            # fail closed: una entrada inválida no puede vaciar la cartera
            return {"error": "cartera inválida", "errors": errors}
        catalog = SourceCatalog(entries)
        self._save_catalog(catalog)
        return {"ok": True, "errors": [],
                "sources": [serialize_source(e) for e in catalog.all()]}

    def add_source(self, name: str, kind: str, location: str,
                   refresh_min: int = 0) -> dict:
        entry, error = self._source_entry({
            "name": name, "kind": kind, "location": location,
            "refresh_min": refresh_min})
        if error is not None:
            return {"error": error}
        assert entry is not None
        catalog = self._catalog()
        catalog.add(entry)
        self._save_catalog(catalog)
        return {"id": entry.id, "name": entry.name, "kind": entry.kind,
                "source_type": entry.source_type, "location": entry.location,
                "refresh_min": entry.refresh_min, "enabled": entry.enabled}

    def update_source(self, source_id: str, name: str | None = None,
                      location: str | None = None,
                      refresh_min: int | None = None,
                      enabled: bool | None = None) -> dict:
        catalog = self._catalog()
        if catalog.get(source_id) is None:
            return {"error": f"fuente {source_id} no existe"}
        changes: dict = {}
        if name is not None:
            changes["name"] = str(name)
        if location is not None:
            error = self._validate_location(location)
            if error:
                return {"error": error}
            changes["location"] = str(location).strip()
            changes["source_type"] = ("url" if changes["location"].lower()
                                      .startswith(("http://", "https://"))
                                      else "file")
        if refresh_min is not None:
            changes["refresh_min"] = max(0, int(refresh_min))
        if enabled is not None:
            changes["enabled"] = bool(enabled)
        updated = catalog.update(source_id, **changes)
        if updated is None:  # ya comprobado arriba; cierra el tipo
            return {"error": f"fuente {source_id} no existe"}
        self._save_catalog(catalog)
        return {"ok": True, "source": serialize_source(updated)}

    def remove_source(self, source_id: str) -> dict:
        catalog = self._catalog()
        if catalog.get(source_id) is None:
            return {"error": f"fuente {source_id} no existe"}
        catalog.remove(source_id)
        self._save_catalog(catalog)
        return {"ok": True}

    def set_target_sources(self, target_id: str, m3u_ids: list[str],
                           epg_ids: list[str], reload: bool = True) -> dict:
        """Selección de filas de la cartera que aporta el target."""
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        if not isinstance(m3u_ids, list) or not isinstance(epg_ids, list):
            return {"error": "m3u_sources y epg_sources deben ser listas"}
        catalog = self._catalog()
        for sid in [*m3u_ids, *epg_ids]:
            if catalog.get(str(sid)) is None:
                return {"error": f"fuente {sid} no existe"}

        def refs(ids):
            return tuple(SourceRef(e.id, e.name, e.location)
                         for e in (catalog.get(str(sid)) for sid in ids) if e)

        new = replace(t, source_refs=refs(m3u_ids), epg_refs=refs(epg_ids))
        return self._write(target_id, new, reload)

    # -- escritura -----------------------------------------------------------

    def _write(self, target_id: str, new: PipelineTarget,
               reload: bool, bind: str | None = None) -> dict:
        """Guarda la definición y, opcionalmente, recarga el contenido servido.

        ``reload=False`` permite a un cliente (la GUI) mandar varios cambios y
        ejecutar el pipeline una sola vez al final con ``run``.
        """
        epg_path = (self._store.get_epg_path(target_id)
                    if self._store is not None else None)
        self._store.save(target_id, new, epg_path=epg_path, bind=bind)
        out: dict = {"ok": True, "target": serialize_target(new),
                     "reloaded": False}
        if reload:
            out["reloaded"] = self._supervisor.reload_target(
                target_id, new, epg_path=epg_path)
            out["metrics"] = self._supervisor.metrics(target_id)
        return out

    def update_settings(self, target_id: str, *, name=None, port=None,
                        bind=None, reload: bool = True) -> dict:
        """Cambia nombre, puerto o bind del target."""
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        changes: dict = {}
        if name is not None:
            changes["name"] = str(name)
        if port is not None:
            try:
                port = int(port)
            except (TypeError, ValueError):
                return {"error": "port debe ser un entero"}
            if not 0 < port < 65536:
                return {"error": "port fuera de rango (1-65535)"}
            changes["port"] = port
        if bind is not None and str(bind) not in ("127.0.0.1", "0.0.0.0"):
            return {"error": "bind debe ser 127.0.0.1 o 0.0.0.0"}
        new = replace(t, **changes)
        return self._write(target_id, new, reload, bind=bind)

    def set_rules(self, target_id: str, rules: list[str],
                  reload: bool = True) -> dict:
        """Reemplaza la lista de filtros (sintaxis xteve textual)."""
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        if not isinstance(rules, (list, tuple)):
            return {"error": "rules debe ser una lista"}
        return self._write(target_id, replace(t, rules=parse_rules(list(rules))),
                           reload)

    def set_mappings(self, target_id: str, mappings: list[dict],
                     reload: bool = True) -> dict:
        """Asignación EPG en lote: ``epg_id`` nulo elimina el mapeo."""
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        if not isinstance(mappings, list):
            return {"error": "mappings debe ser una lista"}
        overrides = dict(t.epg_overrides)
        applied: list[dict] = []
        errors: list[dict] = []
        for item in mappings:
            url = str((item or {}).get("channel_url", "") or "").strip()
            epg_id = (item or {}).get("epg_id")
            if not url:
                errors.append({"error": "channel_url vacío", "mapping": item})
                continue
            if epg_id:
                overrides[url] = str(epg_id)
                applied.append({"channel_url": url, "epg_id": str(epg_id)})
            elif url in overrides:
                del overrides[url]
                applied.append({"channel_url": url, "epg_id": ""})
            else:
                errors.append({"channel_url": url,
                               "error": "sin mapeo que eliminar"})
        out = self._write(target_id, replace(t, epg_overrides=overrides), reload)
        out["applied"] = applied
        out["errors"] = errors
        if errors:
            out["partial"] = True
        return out

    def set_disabled(self, target_id: str, urls: list[str],
                     reload: bool = True) -> dict:
        """Reemplaza la lista de canales desactivados a mano."""
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        if not isinstance(urls, (list, tuple)):
            return {"error": "disabled_channels debe ser una lista"}
        clean = tuple(str(u).strip() for u in urls if str(u).strip())
        return self._write(target_id, replace(t, disabled_channels=clean),
                           reload)

    # -- servicio web del target ---------------------------------------------

    def start_service(self, target_id: str) -> dict:
        """Arranca (o reanuda) el servidor del target; no guarda nada."""
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        epg_path = (self._store.get_epg_path(target_id)
                    if self._store is not None else None)
        ok = self._supervisor.start_target(target_id, t, epg_path=epg_path)
        return {"ok": ok, "status": self.runtime_status(target_id)}

    def stop_service(self, target_id: str) -> dict:
        ok = self._supervisor.stop_target(target_id)
        return {"ok": ok, "status": self.runtime_status(target_id)}


    # -- ejecución -----------------------------------------------------------

    def run(self, target_id: str) -> dict:
        """Ejecuta el pipeline con la definición fresca y devuelve métricas."""
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        epg_path = (self._store.get_epg_path(target_id)
                    if self._store is not None else None)
        if not self._supervisor.reload_target(target_id, t, epg_path=epg_path):
            return {"error": f"target {target_id} no ejecutó"}
        return self._supervisor.metrics(target_id)
