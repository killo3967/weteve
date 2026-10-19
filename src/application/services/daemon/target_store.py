"""TargetStore: CRUD persistente de targets en ``daemon.toml``.

El demonio, la UI y el MCP comparten esta clase; el formato (TOML con esquema)
vive en ``config_toml``: aquí solo se adapta el dominio.

Uso: ``store.load()`` al arrancar; save/delete escriben el fichero al momento.
"""

import logging
from pathlib import Path

from src.application.services.list_manager.models import PipelineTarget
from src.infrastructure.client import config_toml

logger = logging.getLogger(__name__)


class TargetStore:
    """Lee y escribe los ``[[targets]]`` de ``daemon.toml``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._targets: dict[str, PipelineTarget] = {}
        self._epg_paths: dict[str, str | None] = {}
        self._binds: dict[str, str] = {}

    # -- lectura ----------------------------------------------------------------

    def load(self) -> None:
        """Carga todos los targets del fichero (reemplaza lo cacheado)."""
        config = config_toml.load(self._path)
        catalog = config_toml.catalog_from(config)
        targets: dict[str, PipelineTarget] = {}
        epg_paths: dict[str, str | None] = {}
        binds: dict[str, str] = {}
        for raw in config["targets"]:
            target_id = str(raw.get("id", ""))
            if not target_id:
                continue
            targets[target_id] = config_toml.target_from_dict(raw, catalog)
            epg_paths[target_id] = str(raw.get("custom_epg") or "") or None
            binds[target_id] = str(raw.get("bind") or "127.0.0.1")
        self._targets = targets
        self._epg_paths = epg_paths
        self._binds = binds

    @property
    def path(self) -> Path:
        """Ruta de ``daemon.toml`` (el servicio la necesita para la cartera)."""
        return self._path

    # -- acceso -------------------------------------------------------------------

    def list(self) -> dict[str, PipelineTarget]:
        return dict(self._targets)

    def get(self, target_id: str) -> PipelineTarget | None:
        return self._targets.get(target_id)

    def reload(self, target_id: str) -> PipelineTarget | None:
        """Vuelve a leer el fichero y devuelve la definición de ``target_id``.

        Lo que escriba otro proceso (la GUI, un cliente MCP) solo se ve aquí;
        el reload es la forma de reconciliar sin un watcher.
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
        """Inserta o actualiza el target y persiste el fichero.

        Una sola lectura y una sola escritura: el bloque completo del target se
        reemplaza, así que no quedan campos viejos por medio.
        """
        effective_bind = str(bind or self.get_bind(target_id) or "127.0.0.1")
        entry = config_toml.target_to_dict(target, target_id)
        # fuente única (sin cartera): el M3U suelto sigue siendo el source_index
        entry["custom_m3u"] = ("" if target.source_refs
                               else str(target.source_index))
        entry["custom_epg"] = str(epg_path or "")
        entry["bind"] = effective_bind

        def mutate(config: dict) -> None:
            for index, raw in enumerate(config["targets"]):
                if str(raw.get("id")) == str(target_id):
                    config["targets"][index] = entry
                    return
            config["targets"].append(entry)

        config_toml.update(self._path, mutate)
        self._targets[target_id] = target
        self._epg_paths[target_id] = epg_path
        self._binds[target_id] = effective_bind

    def delete(self, target_id: str) -> bool:
        """Elimina el target (cache + fichero). False si no existía."""
        if target_id not in self._targets:
            return False
        config_toml.delete_target(self._path, target_id)
        self._targets.pop(target_id, None)
        self._epg_paths.pop(target_id, None)
        self._binds.pop(target_id, None)
        return True
