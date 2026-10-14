"""LogoCacheService: descarga asíncrona de logos de canales con caché en disco.

Diseño:
  - La UI pide logos con ``request(url)`` (no bloquea nunca: solo encola).
  - Un ThreadPoolExecutor con 10 hilos descarga y valida imágenes.
  - La caché en disco vive en ``cache/logos/``: ficheros nombrados por hash
    SHA-1 de la URL + extensión deducida, con TTL de 7 días (re-descarga).
  - Un índice JSON ``url -> {file, fetched_at}`` evita re-descargar URLs
    frescas y permite podar ficheros huérfanos.
  - La señal ``logo_ready(url, path)`` se emite desde el hilo del pool; Qt la
    entrega en el hilo del receptor (QueuedConnection automática al conectar
    con un slot de un QObject con afinidad de hilo de la UI), por lo que el
    widget puede actualizar la tabla con seguridad de hilos.

Qt-free en la parte de red/disco; solo la emisión usa QtCore.
"""

import hashlib
import json
import logging
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

_TTL_SECONDS = 7 * 24 * 3600  # 7 días
_DOWNLOAD_TIMEOUT = 10  # s
_MAX_WORKERS = 10
_INDEX_NAME = "index.json"
_UA = "weteve-logo-cache/1.0"

# Firmas mágicas para deducir extensión sin depender de mimetypes del server.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"RIFF", ".webp"),  # RIFF....WEBP
)


def _ext_for(data: bytes) -> str:
    """Deducir extensión por firma; fallback .img."""
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if len(data) > 10 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    return ".img"


class LogoCacheService(QObject):
    """Descarga logos en background con caché en disco (TTL 7 días).

    Uso:
        svc = LogoCacheService(cache_dir)
        svc.logo_ready.connect(on_ready)   # on_ready(url: str, path: str)
        svc.request(url)                   # no bloquea
        ...
        svc.shutdown()                     # al cerrar el diálogo
    """

    #: url -> path local listo para usar (se entrega en el hilo del receptor)
    logo_ready = pyqtSignal(str, str)

    def __init__(self, cache_dir: Path | str, parent=None) -> None:
        super().__init__(parent)
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / _INDEX_NAME
        # url -> {"file": str, "fetched_at": float}
        self._index: dict[str, dict] = self._load_index()
        self._pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS,
                                        thread_name_prefix="logo")
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()  # protege _in_flight
        self._closed = False

    # -- API pública -------------------------------------------------------

    def request(self, url: str) -> None:
        """Encola la descarga si no está fresca en caché. No bloquea."""
        if not url or self._closed:
            return
        cached = self._index.get(url)
        if cached and self._is_fresh(cached):
            path = self._dir / cached["file"]
            if path.exists():
                self.logo_ready.emit(url, str(path))
                return
        with self._lock:
            if url in self._in_flight:
                return
            self._in_flight.add(url)
        try:
            self._pool.submit(self._worker, url)
        except RuntimeError:
            # Pool apagándose durante el cierre; no es un error de usuario.
            with self._lock:
                self._in_flight.discard(url)

    def shutdown(self) -> None:
        """Apaga el pool (espera a las descargas en curso, sin cancelar)."""
        if self._closed:
            return
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def cached_path(self, url: str) -> str | None:
        """Ruta local si la URL está fresca en caché, None si no."""
        cached = self._index.get(url)
        if cached and self._is_fresh(cached):
            path = self._dir / cached["file"]
            if path.exists():
                return str(path)
        return None

    # -- workers (hilo de pool) -------------------------------------------

    def _worker(self, url: str) -> None:
        try:
            path = self._download(url)
            if path is not None:
                self.logo_ready.emit(url, str(path))
        except Exception as exc:  # nunca romper el pool
            logger.debug("Logo %s: %s", url, exc)
        finally:
            with self._lock:
                self._in_flight.discard(url)

    def _download(self, url: str) -> Path | None:
        """Descarga y valida la imagen; devuelve la ruta local o None."""
        data = self._fetch(url)
        if not data:
            return None
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        filename = f"{digest}{_ext_for(data)}"
        target = self._dir / filename
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)  # atómico en el mismo volumen
        self._index[url] = {"file": filename, "fetched_at": time.time()}
        self._save_index()
        return target

    def _fetch(self, url: str) -> bytes | None:
        """GET con User-Agent corto, timeout y límite de tamaño (5 MB)."""
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            data = resp.read(5 * 1024 * 1024)
        return data or None

    # -- índice -------------------------------------------------------------

    def _load_index(self) -> dict[str, dict]:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_index(self) -> None:
        try:
            self._index_path.write_text(
                json.dumps(self._index, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logger.warning("No se pudo guardar el índice de logos", exc_info=True)

    def _is_fresh(self, entry: dict) -> bool:
        fetched = entry.get("fetched_at", 0)
        return (time.time() - fetched) < _TTL_SECONDS

    def prune(self) -> None:
        """Borra entradas caducadas (TTL) y ficheros huérfanos del directorio."""
        now = time.time()
        dead_urls = [u for u, e in self._index.items()
                     if now - e.get("fetched_at", 0) >= _TTL_SECONDS]
        for u in dead_urls:
            entry = self._index.pop(u, None)
            if entry:
                try:
                    (self._dir / entry["file"]).unlink(missing_ok=True)
                except OSError:
                    pass
        live_files = {e.get("file") for e in self._index.values()}
        try:
            for f in self._dir.iterdir():
                if f.name == _INDEX_NAME or f.name.endswith(".part"):
                    continue
                if f.name not in live_files:
                    f.unlink(missing_ok=True)
        except OSError:
            pass
        self._save_index()
