import logging
import re
import time
from pathlib import Path

import requests

from src.domain.entities.channel import Channel
from src.domain.ports.i_playlist_repo import IPlaylistRepository

#: Cabecera propia con forma de navegador: los proveedores rechazan clientes
#: sin identificar al descargar la lista por URL.
USER_AGENT = "Mozilla/5.0 (compatible; weteve/1.0)"


def normalize_spacing(text: str) -> str:
    """Limpia espacios: recorta extremos y colapsa secuencias a un espacio.

    "MOVISTAR  CINE" -> "MOVISTAR CINE". Evita que el filtro por subcadena
    falle por diferencias invisibles de espaciado en las listas reales.
    """
    return " ".join(text.split())

class FileM3URepository(IPlaylistRepository):
    """Adaptador que gestiona la persistencia y carga de canales desde archivos M3U.

    Cachea la lista ya parseada (37.600 canales, 0,4 s por recarga), como se
    hace con la EPG: cada recarga del pipeline no tiene por qué releer la
    misma lista.

    - Fichero local: se reutiliza mientras no cambien ``mtime`` ni tamaño.
    - URL: se reutiliza durante ``ttl_seconds`` (5 min por defecto).
    - ``fresh=True`` ignora la cache y vuelve a leer.
    """

    DEFAULT_TTL_SECONDS = 300.0

    def __init__(self, ttl_seconds: float | None = None) -> None:
        self._ttl = (self.DEFAULT_TTL_SECONDS if ttl_seconds is None
                     else float(ttl_seconds))
        self._cache: dict[str, tuple[tuple, list[Channel]]] = {}
        self._uncached = 0

    def _signature(self, source: str) -> tuple:
        """Huella de la fuente: si cambia, hay que volver a leerla."""
        if source.startswith("http"):
            if self._ttl <= 0:
                self._uncached += 1
                return ("url-sin-cache", self._uncached)
            return ("url", int(time.monotonic() // self._ttl))
        try:
            stat = Path(source).stat()
        except OSError:
            return ("file-ausente", source)
        return ("file", stat.st_mtime_ns, stat.st_size)

    def get_channels(self, source: str, fresh: bool = False) -> list[Channel]:
        """Carga los canales desde una URL o un archivo local (con cache)."""
        signature = self._signature(source)
        cached = None if fresh else self._cache.get(source)
        if cached is not None and cached[0] == signature:
            return list(cached[1])
        channels = self._parse_m3u(self._get_content(source))
        self._cache[source] = (signature, channels)
        return list(channels)

    def _get_content(self, source: str) -> list[str]:
        """Obtiene las líneas del contenido M3U."""
        if source.startswith('http'):
            # Descarga remota
            try:
                headers = {'User-Agent': USER_AGENT}
                # requests detecta proxies de variables de entorno automáticamente
                response = requests.get(source, headers=headers, timeout=15)
                response.raise_for_status()
                return response.text.splitlines()
            except Exception as e:
                logging.error(f"Error descargando M3U: {e}")
                return []
        else:
            # Archivo local
            path = Path(source)
            if not path.exists():
                return []
            with open(path, encoding='utf-8') as f:
                return f.readlines()

    def _parse_m3u(self, lines: list[str]) -> list[Channel]:
        """Lógica de parseo de M3U extraída del código original."""
        channels = []
        i = 0
        pending_extgrp: list[str] = []  # herencia de la directiva #EXTGRP
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('#EXTGRP'):
                # #EXTGRP:Grupo añade grupo a los siguientes canales;
                # vacío lo limpia.
                value = line[len('#EXTGRP:'):].strip() if ':' in line else ""
                pending_extgrp = [normalize_spacing(value)] if value else []
                i += 1
                continue
            if line.startswith('#EXTINF'):
                info = self._parse_extinf(line)

                # Buscar la URL real
                j = i + 1
                while j < len(lines) and (lines[j].strip().startswith('#') or not lines[j].strip()):
                    j += 1

                url = lines[j].strip() if j < len(lines) else ""

                if url:
                    name = normalize_spacing(
                        info.get('tvg-name') or info.get('display-name')
                        or info.get('tvg-id') or "")
                    raw_group = normalize_spacing(info.get('group-title', ''))
                    # Primario = primer group-title (los múltiples van por ';')
                    group = raw_group.split(';')[0].strip() or "Otros"

                    # Campos de agrupación presentes en ESTA línea EXTINF.
                    # Un group-title propio reinicia la herencia de #EXTGRP.
                    groups: dict[str, list[str]] = {}
                    if raw_group:
                        groups["group-title"] = [
                            normalize_spacing(g) for g in raw_group.split(';')
                            if normalize_spacing(g)
                        ]
                    tvg_group = normalize_spacing(info.get('tvg-group', ''))
                    if tvg_group:
                        groups["tvg-group"] = [
                            normalize_spacing(g) for g in tvg_group.split(';')
                            if normalize_spacing(g)
                        ]
                    if pending_extgrp and "group-title" not in groups:
                        groups["extgrp"] = list(pending_extgrp)

                    channels.append(Channel(
                        name=name or "Canal sin nombre",
                        url=url,
                        group=group,
                        logo_url=info.get('tvg-logo'),
                        tvg_id=info.get('tvg-id'),
                        groups=groups,
                    ))
                i = j
            else:
                i += 1
        return channels

    def _parse_extinf(self, line: str) -> dict:
        """Extrae atributos de la línea #EXTINF."""
        patterns = {
            'tvg-id': r'tvg-id="(.*?)"',
            'tvg-name': r'tvg-name="(.*?)"',
            'tvg-logo': r'tvg-logo="(.*?)"',
            'group-title': r'group-title="(.*?)"',
            'tvg-group': r'tvg-group="(.*?)"',
        }
        results = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, line)
            results[key] = match.group(1) if match else ""

        # Extraer nombre tras la última coma (formato: ...,Channel Name)
        # Útil para listas que no usan tvg-name (ej: Pluto TV)
        comma_idx = line.rfind(',')
        if comma_idx != -1:
            display_name = line[comma_idx + 1:].strip()
            if display_name:
                results['display-name'] = display_name

        return results
