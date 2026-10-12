"""TargetStore: CRUD persistente de targets en config.ini.

Reemplaza la lógica duplicada en main.py (load_config/save_config): mismo
formato de secciones ``listmanager.target.N`` para compatibilidad total con lo
ya guardado por la UI. El demonio, la UI y el MCP comparten esta clase.

Uso: ``store.load()`` al arrancar; save/delete escriben el fichero inmediatamente.
"""

import configparser
import logging
from pathlib import Path

from src.application.services.list_manager.models import FilterRule, PipelineTarget, SourceRef
from src.infrastructure.client.sources_store import read_catalog, refs_for_target, selection_ids

logger = logging.getLogger(__name__)

_TARGET_PREFIX = "listmanager.target."


def _refs_for(parser: configparser.ConfigParser, target_id: str,
              kind: str) -> tuple[SourceRef, ...]:
    """Selección ``m3u_sources``/``epg_sources`` del target como SourceRefs."""
    return refs_for_target(parser, f"{_TARGET_PREFIX}{target_id}", kind)

class TargetStore:
    """Lee y escribe ``listmanager.target.N`` en config.ini."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._targets: dict[str, PipelineTarget] = {}
        self._epg_paths: dict[str, str | None] = {}
        self._binds: dict[str, str] = {}

    # -- lectura ----------------------------------------------------------------

    def load(self) -> None:
        """Carga todos los targets del fichero (reemplaza lo cacheado)."""
        parser = configparser.ConfigParser()
        if self._path.exists():
            parser.read(self._path, encoding="utf-8")

        targets: dict[str, PipelineTarget] = {}
        epg_paths: dict[str, str | None] = {}
        binds: dict[str, str] = {}
        for sec in parser.sections():
            if not sec.startswith(_TARGET_PREFIX):
                continue
            tid = sec[len(_TARGET_PREFIX):]
            raw_rules = parser.get(sec, "rules", fallback="")
            rules = tuple(
                FilterRule.parse(r.strip()) for r in raw_rules.split("|") if r.strip()
            )
            raw_overrides = parser.get(sec, "epg_overrides", fallback="")
            overrides: dict[str, str] = {}
            for pair in raw_overrides.split(";;"):
                if "::" in pair:
                    url, cid = pair.split("::", 1)
                    overrides[url] = cid
            saved_epg_src = parser.get(sec, "epg_source", fallback="-1")
            targets[tid] = PipelineTarget(
                name=parser.get(sec, "name", fallback=f"Target {tid}"),
                source_index=parser.get(sec, "custom_m3u", fallback=""),
                rules=rules,
                epg_source_index=(int(saved_epg_src)
                                  if saved_epg_src.isdigit() else None),
                epg_overrides=overrides,
                port=parser.getint(sec, "port", fallback=8080),
                source_refs=_refs_for(parser, tid, "m3u"),
                epg_refs=_refs_for(parser, tid, "epg"),
            )
            epg_paths[tid] = parser.get(sec, "custom_epg", fallback="") or None
            binds[tid] = parser.get(sec, "bind", fallback="127.0.0.1")
        self._targets = targets
        self._epg_paths = epg_paths
        self._binds = binds

    # -- acceso -------------------------------------------------------------------

    def list(self) -> dict[str, PipelineTarget]:
        return dict(self._targets)

    def get(self, target_id: str) -> PipelineTarget | None:
        return self._targets.get(target_id)

    def reload(self, target_id: str) -> PipelineTarget | None:
        """Vuelve a leer config.ini y devuelve la definición de ``target_id``.

        El daemon lee el fichero una sola vez al arrancar; lo que escriba
        después otro proceso (la GUI, un cliente MCP) solo se ve aquí. Ninguno
        de los dos es dueño del fichero, así que el reload es la forma de
        reconciliar sin un watcher.
        """
        self.load()
        return self._targets.get(target_id)

    def get_epg_path(self, target_id: str) -> str | None:
        return self._epg_paths.get(target_id)

    def get_bind(self, target_id: str) -> str:
        return self._binds.get(target_id, "127.0.0.1")

    # -- escritura ------------------------------------------------------------------

    def save(self, target_id: str, target: PipelineTarget,
             epg_path: str | None = None, bind: str | None = None) -> None:
        """Inserta o actualiza el target y persiste el fichero."""
        parser = configparser.ConfigParser()
        if self._path.exists():
            parser.read(self._path, encoding="utf-8")

        rules_str = "|".join(r.serialize() for r in target.rules)
        overrides_str = ";;".join(
            f"{u}::{c}" for u, c in target.epg_overrides.items()
        )
        epg_source = target.epg_source_index
        catalog = read_catalog(parser)
        parser[f"{_TARGET_PREFIX}{target_id}"] = {
            "name": target.name,
            "custom_m3u": str(target.source_index),
            "custom_epg": str(epg_path or ""),
            "epg_source": str(epg_source if epg_source is not None else -1),
            "rules": rules_str,
            "epg_overrides": overrides_str,
            "port": str(target.port),
            "bind": str(bind or self.get_bind(target_id) or "127.0.0.1"),
            "m3u_sources": selection_ids(target.source_refs, catalog),
            "epg_sources": selection_ids(target.epg_refs, catalog),
        }
        with open(self._path, "w", encoding="utf-8") as f:
            parser.write(f)

        self._targets[target_id] = target
        self._epg_paths[target_id] = epg_path
        self._binds[target_id] = str(bind or self.get_bind(target_id) or "127.0.0.1")

    def delete(self, target_id: str) -> bool:
        """Elimina el target (cache + sección en fichero). False si no existía."""
        if target_id not in self._targets:
            return False
        parser = configparser.ConfigParser()
        if self._path.exists():
            parser.read(self._path, encoding="utf-8")
        sec = f"{_TARGET_PREFIX}{target_id}"
        if parser.has_section(sec):
            parser.remove_section(sec)
            with open(self._path, "w", encoding="utf-8") as f:
                parser.write(f)
        self._targets.pop(target_id, None)
        self._epg_paths.pop(target_id, None)
        self._binds.pop(target_id, None)
        return True
