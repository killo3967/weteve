import gzip
import io
import logging
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

import requests

from src.domain.entities.epg import EPGData, Program
from src.domain.ports.i_epg_repo import IEPGRepository
from src.infrastructure.utils.sevenzip import extract_7z


class XMLTVRepository(IEPGRepository):
    """Adaptador de infraestructura para cargar datos EPG desde archivos XMLTV.

    Cachea la guía ya parseada: cambiar una regla de filtro reejecuta el
    pipeline pero no cambia las guías, y volver a descargar y parsear 100.000
    programas en cada clic se nota (segundos).

    - Fichero local: se reutiliza mientras no cambien ``mtime`` ni tamaño.
    - URL: se reutiliza durante ``ttl_seconds`` (5 min por defecto, el mismo
      suelo que el planificador de recargas). ``ttl_seconds=0`` desactiva la
      cache, útil para forzar una descarga fresca.
    """

    DEFAULT_TTL_SECONDS = 300.0

    def __init__(self, ttl_seconds: float | None = None) -> None:
        self._ttl = (self.DEFAULT_TTL_SECONDS if ttl_seconds is None
                     else float(ttl_seconds))
        self._cache: dict[str, tuple[tuple, EPGData]] = {}
        # contador para el modo sin cache (monotonic en Windows tiene ~16 ms
        # de resolución: dos llamadas seguidas darían la misma firma)
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

    def load_epg(self, source: str) -> EPGData:
        """Carga, descarga y parsea una fuente XMLTV (con cache)."""
        signature = self._signature(source)
        cached = self._cache.get(source)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            content = self._get_content(source)
            if not content:
                return EPGData()
            epg = self._parse_xmltv(content)
        except Exception as e:
            # Un fallo no se cachea: en la siguiente recarga se reintenta.
            logging.error(f"Error cargando EPG desde {source}: {e}")
            return EPGData()
        self._cache[source] = (signature, epg)
        return epg

    def _get_content(self, source: str) -> bytes:
        """Descarga el contenido y lo descomprime (gz/zip/7z) si es necesario."""
        data = self._read_source(source)
        if not data:
            return b""
        return self._decompress(data, source)

    def _read_source(self, source: str) -> bytes:
        """Obtiene los bytes crudos desde una URL o un archivo local."""
        if source.startswith('http'):
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(source, headers=headers, timeout=20)
                response.raise_for_status()
                return response.content
            except Exception as e:
                logging.error(f"Error descargando EPG remota: {e}")
                return b""
        path = Path(source)
        if not path.exists():
            return b""
        with open(path, 'rb') as f:
            return f.read()

    def _decompress(self, data: bytes, source: str) -> bytes:
        """Descomprime ``data`` según la extensión de ``source`` (o su magic)."""
        lower = source.lower()
        if lower.endswith('.gz') or data.startswith(b'\x1f\x8b'):
            try:
                return gzip.decompress(data)
            except Exception as e:
                logging.error(f"Error descomprimiendo GZIP: {e}")
                return b""
        if lower.endswith('.zip') or data.startswith(b'PK\x03\x04'):
            return self._extract_zip(data)
        if lower.endswith('.7z') or data.startswith(b'7z\xbc\xaf\x27\x1c'):
            return self._extract_7z(data)
        return data

    def _extract_zip(self, data: bytes) -> bytes:
        """Extrae el primer XML/XMLTV de un archivo .zip."""
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(('.xml', '.xmltv')):
                        return self._decompress_member(zf.read(name), name)
                for name in zf.namelist():
                    content = zf.read(name)
                    if content.lstrip().startswith(b'<'):
                        return self._decompress_member(content, name)
        except Exception as e:
            logging.error(f"Error descomprimiendo ZIP: {e}")
        return b""

    def _extract_7z(self, data: bytes) -> bytes:
        """Extrae el primer XML/XMLTV de un archivo .7z (vía tar.exe/7-Zip)."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="epg_7z_"))
        try:
            archive = tmp_dir / "epg.7z"
            archive.write_bytes(data)
            out_dir = tmp_dir / "out"
            out_dir.mkdir()
            extract_7z(archive, out_dir)
            return self._find_xml(out_dir)
        except Exception as e:
            logging.error(f"Error descomprimiendo 7z: {e}")
            return b""
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _find_xml(self, root: Path) -> bytes:
        """Busca recursivamente el primer XML/XMLTV dentro de ``root``."""
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.suffix.lower() in ('.xml', '.xmltv'):
                return self._decompress_member(path.read_bytes(), str(path))
        for path in sorted(root.rglob('*')):
            if path.is_file():
                content = path.read_bytes()
                if content.lstrip().startswith(b'<'):
                    return self._decompress_member(content, str(path))
        return b""

    def _decompress_member(self, content: bytes, name: str) -> bytes:
        """Descomprime un miembro si viene a su vez en .gz (doble compresión)."""
        if name.lower().endswith('.gz') or content.startswith(b'\x1f\x8b'):
            try:
                return gzip.decompress(content)
            except Exception:
                return content
        return content

    def _parse_xmltv(self, content: bytes) -> EPGData:
        """Parsea la estructura XMLTV."""
        programs = []
        channel_names = {}
        try:
            root = ET.fromstring(content)

            # 1. Mapear IDs de canales a nombres
            for chan_elem in root.findall('channel'):
                chan_id = chan_elem.get('id')
                display_name = chan_elem.find('display-name')
                if chan_id and display_name is not None:
                    channel_names[chan_id] = display_name.text or ""

            # 2. Parsear programas
            for prog_elem in root.findall('programme'):
                channel_id = prog_elem.get('channel')
                start_str = prog_elem.get('start')
                end_str = prog_elem.get('stop')

                if not channel_id or not start_str or not end_str:
                    continue

                title_elem = prog_elem.find('title')
                title = (title_elem.text or "Sin título") if title_elem is not None else "Sin título"

                desc_elem = prog_elem.find('desc')
                description = desc_elem.text if desc_elem is not None else ""

                cat_elem = prog_elem.find('category')
                category = cat_elem.text if cat_elem is not None else "Otros"

                programs.append(Program(
                    title=title,
                    start_time=self._parse_date(start_str),
                    end_time=self._parse_date(end_str),
                    channel_id=channel_id,
                    description=description,
                    category=category
                ))

            logging.info(f"EPG parseada: {len(programs)} programas y {len(channel_names)} canales mapeados.")
            return EPGData(programs, channel_names)
        except Exception as e:
            logging.error(f"Error parseando XMLTV: {e}")
            return EPGData()

    def _parse_date(self, date_str: str) -> datetime:
        """Convierte fechas estándar XMLTV (YYYYMMDDHHMMSS +HHMM) a datetime."""
        # Ejemplo: 20260305110000 +0100
        # Tomamos los primeros 14 carácteres para simplificar (ignorando zona horaria por ahora)
        clean_date = date_str.split(' ')[0][:14]
        return datetime.strptime(clean_date, "%Y%m%d%H%M%S")
