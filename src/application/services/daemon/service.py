"""TargetService: operaciones de dominio sobre los targets.

Única capa con lógica entre el supervisor/pipeline y los transportes. La API
REST y el registro de herramientas MCP son adaptadores finos: traducen
HTTP/JSON-RPC a llamadas de esta clase, sin reglas de negocio propias.

Las lecturas salen del snapshot que mantiene el supervisor (no reejecutan el
pipeline). Solo ``run`` ejecuta.
"""

import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import cast

from src.application.services.list_manager.models import (
    ChannelProfile,
    FilterRule,
    PipelineTarget,
    SourceRef,
    url_key,
)
from src.application.services.list_manager.source_catalog import (
    SourceCatalog,
    SourceEntry,
    SourceKind,
    SourceType,
    infer_source_type,
    new_source_id,
)

logger = logging.getLogger(__name__)

#: Extensiones que el demonio sirve como logo desde ``assets/logos``.
LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


def logos_dir() -> Path:
    """Directorio de logos: el mismo que sirve el demonio en ``/logos/``."""
    return Path(__file__).resolve().parents[4] / "assets" / "logos"


def _nombre_seguro(nombre: str) -> str:
    """Nombre de fichero sin rutas ni caracteres raros."""
    import re

    limpio = re.sub(r"[^A-Za-z0-9._-]", "_", Path(nombre).name)
    return limpio or "logo.png"


def _nombre_libre(destino: Path) -> str:
    """``X.png`` ocupado por otro fichero → ``X_2.png``."""
    for n in range(2, 100):
        candidato = f"{destino.stem}_{n}{destino.suffix}"
        if not (destino.parent / candidato).exists():
            return candidato
    return f"{destino.stem}_{destino.stat().st_mtime_ns}{destino.suffix}"


def logo_valido(nombre: str) -> bool:
    """True si el nombre es un fichero de imagen de ``assets/logos``.

    Se guarda solo el nombre, no una ruta: el demonio sirve ese directorio y
    el reproductor pide ``/logos/<fichero>``. Una ruta absoluta no viajaría.
    """
    limpio = (nombre or "").strip()
    if (not limpio or "/" in limpio or "\\" in limpio
            or limpio.startswith(".")):
        return False
    if not limpio.lower().endswith(LOGO_EXTENSIONS):
        return False
    return (logos_dir() / limpio).is_file()


def serialize_target(t: PipelineTarget,
                     urls: dict[str, list[str]] | None = None) -> dict:
    """PipelineTarget → dict JSON con reglas en sintaxis xteve textual.

    ``urls`` es ``identidad -> [urls]`` del último snapshot: con él las fichas
    se pliegan a url y el cliente (UI, MCP) ve el mapeo y el apagado como
    siempre, aunque en el fichero vivan por identidad. Sin snapshot se publica
    solo lo que ya está por url.
    """
    datos: dict = {
        "name": t.name,
        "m3u": t.source_index,
        "rules": [r.serialize() for r in t.rules],
        "epg_overrides": dict(t.epg_overrides),
        "disabled_channels": list(t.disabled_channels),
        "channels": {clave: {"alias": f.alias, "dial": f.dial,
                             "display": f.display, "logo": f.logo,
                             "epg": f.epg, "enabled": f.enabled}
                     for clave, f in t.channels.items()},
        "serve_without_epg": bool(t.serve_without_epg),
        "light_epg": bool(t.light_epg),
        "quality_priority": dict(t.quality_priority or {}),
        "port": t.port,
        "starting_number": t.starting_number,
        "source_refs": [{"id": r.id, "name": r.name, "location": r.location}
                        for r in t.source_refs],
        "epg_refs": [{"id": r.id, "name": r.name, "location": r.location}
                     for r in t.epg_refs],
    }
    if urls:
        overrides: dict[str, str] = dict(datos["epg_overrides"])
        disabled: list[str] = list(datos["disabled_channels"])
        for identidad, ficha in t.channels.items():
            for url in urls.get(identidad, ()):
                if ficha.epg:
                    overrides.setdefault(url, ficha.epg)
                if ficha.enabled is False and url not in disabled:
                    disabled.append(url)
        datos["epg_overrides"] = overrides
        datos["disabled_channels"] = disabled
    return datos


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
        "priority": entry.priority,
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
        """Definición desde disco cuando se pueda (daemon.toml lo editan otros).

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

    def _identidades(self, target_id: str) -> dict[str, str]:
        """``url -> identidad`` del último pipeline (vacío si no ha corrido)."""
        getter = getattr(self._supervisor, "url_identities", None)
        return getter(target_id) if getter is not None else {}

    def _urls_por_identidad(self, target_id: str) -> dict[str, list[str]]:
        """``identidad -> [urls]`` del último snapshot del perfil.

        Sirve para plegar las fichas a url al publicar el target (la UI y el
        MCP siguen hablando de urls). Vacío si el supervisor no lo sabe todavía
        (pipeline sin ejecutar) o si es un doble de test.
        """
        getter = getattr(self._supervisor, "urls_for_identity", None)
        return getter(target_id) if getter is not None else {}

    def status(self) -> dict:
        """Estado agregado de los targets (running, URLs, errores)."""
        return self._supervisor.get_status()

    def list_targets(self) -> dict:
        targets = self._store.list() if self._store is not None else {}
        return {"targets": {tid: serialize_target(
            t, self._urls_por_identidad(tid))
            for tid, t in targets.items()}}

    def get_target(self, target_id: str) -> dict | None:
        t = self._definition(target_id)
        if t is None:
            return None
        return {"target": serialize_target(
            t, self._urls_por_identidad(target_id))}

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

    def epg_catalog(self, target_id: str, limit: int = 200,
                    offset: int = 0) -> dict:
        """Catálogo de la guía con dial, código interno y si está en la lista."""
        return self._supervisor.epg_catalog(
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
            raise RuntimeError("configuración del demonio no disponible")
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
        if source_type not in ("file", "url", "movistar"):
            source_type = infer_source_type(location)
        source_type = cast(SourceType, source_type)
        try:
            refresh = max(0, int(raw.get("refresh_min") or 0))
        except (TypeError, ValueError):
            refresh = 0
        try:
            priority = int(raw.get("priority") or 0)
        except (TypeError, ValueError):
            priority = 0
        return SourceEntry(
            id=str(raw.get("id") or new_source_id()),
            name=str(raw.get("name") or location),
            kind=kind,
            source_type=source_type,
            location=location,
            refresh_min=refresh,
            enabled=bool(raw.get("enabled", True)),
            priority=priority,
        ), None

    def sources(self, kind: str | None = None) -> dict:
        entries = self._catalog().all()
        if kind:
            entries = [e for e in entries if e.kind == kind]
        return {"sources": [serialize_source(e) for e in entries]}

    def set_sources(self, sources: list[dict]) -> dict:
        """Reemplaza la cartera completa; los ids que llegan se conservan."""
        if self._config_path is None:
            return {"error": "configuración del demonio no disponible"}
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
                   refresh_min: int = 0, priority: int = 0) -> dict:
        entry, error = self._source_entry({
            "name": name, "kind": kind, "location": location,
            "refresh_min": refresh_min, "priority": priority})
        if error is not None:
            return {"error": error}
        assert entry is not None
        catalog = self._catalog()
        catalog.add(entry)
        self._save_catalog(catalog)
        return serialize_source(entry)

    def update_source(self, source_id: str, name: str | None = None,
                      location: str | None = None,
                      refresh_min: int | None = None,
                      enabled: bool | None = None,
                      priority: int | None = None) -> dict:
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
            changes["source_type"] = infer_source_type(changes["location"])
        if refresh_min is not None:
            changes["refresh_min"] = max(0, int(refresh_min))
        if priority is not None:
            changes["priority"] = int(priority)
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
            return tuple(SourceRef(e.id, e.name, e.location, e.priority)
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
        t0 = time.perf_counter()
        self._store.save(target_id, new, epg_path=epg_path, bind=bind)
        t_file = time.perf_counter()
        out: dict = {
            "ok": True,
            "target": serialize_target(
                new, self._urls_por_identidad(target_id)),
            "reloaded": False,
        }
        if reload:
            out["reloaded"] = self._supervisor.reload_target(
                target_id, new, epg_path=epg_path)
            t_reload = time.perf_counter()
            logger.info("guardado target %s: fichero=%.2fs recarga=%.2fs",
                        target_id, t_file - t0, t_reload - t_file)
            out["metrics"] = self._supervisor.metrics(target_id)
        return out

    def update_settings(self, target_id: str, *, name=None, port=None,
                        bind=None, quality_priority=None,
                        serve_without_epg=None, light_epg=None,
                        reload: bool = True) -> dict:
        """Cambia nombre, puerto, bind, prioridad, dummy o guía ligera."""
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
        if quality_priority is not None:
            if not isinstance(quality_priority, dict):
                return {"error": "quality_priority debe ser un objeto"}
            tabla: dict[str, int] = {}
            for token, peso in quality_priority.items():
                try:
                    valor = int(peso)
                except (TypeError, ValueError):
                    return {"error": f"prioridad no numérica para {token!r}"}
                if not 0 <= valor <= 9:
                    return {"error": "las prioridades van de 0 a 9"}
                tabla[str(token)] = valor
            changes["quality_priority"] = tabla or None
        if serve_without_epg is not None:
            changes["serve_without_epg"] = bool(serve_without_epg)
        if light_epg is not None:
            changes["light_epg"] = bool(light_epg)
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
        """Asignación EPG en lote: ``epg_id`` nulo elimina el mapeo.

        La decisión se guarda en la **ficha del canal** (por identidad) cuando
        el último pipeline conoce su url; si no la conoce, se guarda en el
        formato viejo por url. Nunca se pierde: lo que no se puede traducir se
        queda donde estaba.
        """
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        if not isinstance(mappings, list):
            return {"error": "mappings debe ser una lista"}
        overrides = dict(t.epg_overrides)
        fichas = dict(t.channels)
        identidades = self._identidades(target_id)
        applied: list[dict] = []
        errors: list[dict] = []
        for item in mappings:
            url = str((item or {}).get("channel_url", "") or "").strip()
            epg_id = (item or {}).get("epg_id")
            if not url:
                errors.append({"error": "channel_url vacío", "mapping": item})
                continue
            identidad = identidades.get(url)
            if identidad:
                ficha = fichas.get(identidad) or ChannelProfile()
                if epg_id:
                    fichas[identidad] = replace(ficha, epg=str(epg_id))
                    applied.append({"channel_url": url, "epg_id": str(epg_id)})
                elif ficha.epg:
                    fichas[identidad] = replace(ficha, epg="")
                    applied.append({"channel_url": url, "epg_id": ""})
                elif url in overrides:
                    del overrides[url]       # venía del formato viejo
                    applied.append({"channel_url": url, "epg_id": ""})
                else:
                    errors.append({"channel_url": url,
                                   "error": "sin mapeo que eliminar"})
                # la decisión ya vive en la ficha: el legado sobra
                overrides.pop(url, None)
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
        out = self._write(target_id, replace(
            t, epg_overrides=overrides, channels=fichas), reload)
        out["applied"] = applied
        out["errors"] = errors
        if errors:
            out["partial"] = True
        return out

    def _resolver_logo(self, valor: str) -> tuple[str, str | None]:
        """Logo pedido por el usuario → nombre de fichero en ``assets/logos``.

        Acepta las tres formas que se usan a mano: el **nombre** de un fichero
        que ya está en la carpeta, una **ruta** a una imagen (se copia) y una
        **URL http(s)** (se descarga). Devuelve (nombre, error).
        """
        limpio = (valor or "").strip()
        if not limpio:
            return "", None
        carpeta = logos_dir()
        carpeta.mkdir(parents=True, exist_ok=True)

        if limpio.lower().startswith(("http://", "https://")):
            from src.application.services.list_manager.logo_store import LogoStore

            mapa = LogoStore(carpeta).ensure([limpio])
            nombre = mapa.get(limpio)
            if not nombre:
                return "", f"no se pudo descargar el logo {limpio!r}"
            return nombre, None

        ruta = Path(limpio)
        if ruta.is_file():
            if not ruta.name.lower().endswith(LOGO_EXTENSIONS):
                return "", f"{ruta.name!r} no tiene extensión de imagen"
            destino = carpeta / _nombre_seguro(ruta.name)
            if destino.exists() and destino.read_bytes() != ruta.read_bytes():
                destino = carpeta / _nombre_libre(destino)
            if not destino.exists():
                import shutil

                shutil.copy2(ruta, destino)
            return destino.name, None

        if logo_valido(limpio):
            return limpio, None
        return "", (f"{limpio!r} no es una imagen de assets/logos, ni una ruta, "
                    "ni una URL")

    def set_channels(self, target_id: str, channels: list[dict],
                     reload: bool = True) -> dict:
        """Fija a mano dial, nombre mostrado y logo de varios canales.

        Cada entrada es ``{"identity", "url", "dial", "display", "logo"}``. Un
        campo ausente no se toca; ``None`` o vacío lo devuelve al automático.
        Se aplica todo o nada: si un dial se repite, no se guarda nada.
        """
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        if not isinstance(channels, list):
            return {"error": "channels debe ser una lista"}
        fichas = dict(t.channels)
        errores: list[dict] = []
        for item in channels:
            fila = dict(item or {})
            clave = str(fila.get("identity") or "").strip()
            if not clave:
                url = str(fila.get("url") or "").strip()
                if not url:
                    errores.append({"error": "canal sin identidad ni url",
                                    "channel": fila})
                    continue
                clave = url_key(url)
            cambios: dict = {}
            if "dial" in fila:
                valor = fila.get("dial")
                if valor in (None, ""):
                    cambios["dial"] = None
                else:
                    try:
                        dial = int(valor)
                    except (TypeError, ValueError):
                        errores.append({"error": "dial no numérico",
                                        "channel": clave, "value": valor})
                        continue
                    if not 1 <= dial <= 9999:
                        errores.append({"error": "el dial va de 1 a 9999",
                                        "channel": clave, "value": valor})
                        continue
                    cambios["dial"] = dial
            if "display" in fila:
                cambios["display"] = str(fila.get("display") or "").strip()
            if "logo" in fila:
                logo, fallo = self._resolver_logo(str(fila.get("logo") or ""))
                if fallo:
                    errores.append({"error": fallo, "channel": clave,
                                    "value": fila.get("logo")})
                    continue
                cambios["logo"] = logo
            fichas[clave] = replace(fichas.get(clave) or ChannelProfile(),
                                    **cambios)
        if errores:
            return {"error": "hay canales con datos no válidos",
                    "errors": errores}
        conflictos = self._diales_repetidos(target_id, fichas)
        if conflictos:
            return {"error": "dial repetido: dos canales no pueden compartir "
                             "número", "conflicts": conflictos}
        out = self._write(target_id, replace(t, channels=fichas), reload)
        out["channels"] = len(fichas)
        return out

    def _diales_repetidos(self, target_id: str,
                          fichas: dict[str, ChannelProfile]) -> list[dict]:
        """Canales que acabarían con el mismo número.

        Se compara el número final de la última ejecución (dial de la guía o
        cola) con los forzados de la ficha: un forzado que pise el dial de otro
        canal es un choque; dos forzados iguales, también. Sin ejecución
        previa solo se pueden comparar los forzados entre sí.
        """
        filas: list[dict] = []
        try:
            page = self._supervisor.mapping_candidates(
                target_id, limit=5000, include_served=True)
            filas = list(page.get("channels") or [])
        except Exception:  # noqa: BLE001 - sin snapshot se valida lo forzado
            logger.warning("Sin snapshot para validar diales de %s", target_id)
        numeros: dict[int, list[str]] = {}
        for fila in filas:
            clave = str(fila.get("identity") or "")
            if not clave:
                continue
            ficha = fichas.get(clave)
            forzado = ficha.dial if ficha is not None else None
            if fila.get("served") is False and forzado is None:
                # una variante descartada (o un canal que no se sirve) no
                # ocupa número: solo cuentan las filas servidas
                continue
            numero = forzado if forzado is not None else fila.get("dial_auto")
            if numero is None:
                continue
            numeros.setdefault(int(numero), []).append(
                str(fila.get("name") or clave))
        if not filas:
            for clave, ficha in fichas.items():
                if ficha.dial is not None:
                    numeros.setdefault(int(ficha.dial), []).append(clave)
        return [{"dial": numero, "channels": nombres}
                for numero, nombres in sorted(numeros.items())
                if len(nombres) > 1]

    def set_disabled(self, target_id: str, urls: list[str],
                     reload: bool = True) -> dict:
        """Reemplaza la lista de canales desactivados a mano.

        Igual que los mapeos: el apagado se guarda en la ficha del canal
        (``enabled = false``) cuando el último pipeline conoce su url, y en el
        formato viejo por url cuando no. Reactivar quita la decisión de la
        ficha, pero solo en canales que este pipeline ha visto: de los que no
        están en la lista no se toca nada.
        """
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        if not isinstance(urls, (list, tuple)):
            return {"error": "disabled_channels debe ser una lista"}
        clean = tuple(str(u).strip() for u in urls if str(u).strip())
        identidades = self._identidades(target_id)
        conocidas = set(identidades.values())
        fichas = dict(t.channels)
        apagadas: set[str] = set()
        restantes: list[str] = []
        for url in clean:
            identidad = identidades.get(url)
            if identidad:
                fichas[identidad] = replace(
                    fichas.get(identidad) or ChannelProfile(), enabled=False)
                apagadas.add(identidad)
            else:
                restantes.append(url)
        for identidad, ficha in list(fichas.items()):
            if (ficha.enabled is False and identidad not in apagadas
                    and identidad in conocidas):
                fichas[identidad] = replace(ficha, enabled=None)
        return self._write(target_id, replace(
            t, disabled_channels=tuple(restantes), channels=fichas), reload)

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

    def refresh_epg(self, target_id: str) -> dict:
        """Vuelve a leer las guías del target (descarga si son URLs).

        Es la única operación que descarga guías aparte del planificador:
        filtrar, mapear o desactivar canales reutiliza la que ya está cargada.
        """
        t = self._definition(target_id)
        if t is None:
            return {"error": f"target {target_id} no existe"}
        epg_path = (self._store.get_epg_path(target_id)
                    if self._store is not None else None)
        if not self._supervisor.reload_target(
                target_id, t, epg_path=epg_path, refresh_epg=True):
            return {"error": f"target {target_id} no recargó las guías"}
        return {"ok": True, "metrics": self._supervisor.metrics(target_id)}

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
