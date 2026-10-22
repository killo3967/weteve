"""Copia local de los logos de canal.

El logo que publica una guía o un proveedor es una URL de un tercero: puede
cambiar de forma, pedir ``Referer`` o desaparecer. El demonio se queda una
copia en disco (``assets/logos``) y sirve la suya en ``/logos/<fichero>``, así
que la lista que ve el reproductor no depende de nadie.

Solo se descarga lo que falta: un fichero ya presente y con firma de imagen
válida no se vuelve a pedir. Los fallos no rompen la recarga: ese canal se
queda con su URL remota.
"""

import logging
import re
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# Primeros bytes de los formatos que aceptamos como imagen.
_FIRMAS = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",  # JPEG
    b"GIF8",
    b"RIFF",          # WEBP (RIFF....WEBP)
    b"<svg",
    b"<?xml",         # SVG con declaración
)

_EXTENSIONES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")

#: Tope de descarga por logo: ninguno pasa de unos pocos KB.
MAX_BYTES = 2 * 1024 * 1024


class LogoStore:
    """Descarga los logos una vez y devuelve su nombre de fichero local."""

    def __init__(self, directory: Path | str, timeout: float = 10.0,
                 opener=None) -> None:
        self._dir = Path(directory)
        self._timeout = float(timeout)
        self._opener = opener or self._download
        self._cache: dict[str, str | None] = {}

    @property
    def directory(self) -> Path:
        return self._dir

    def ensure(self, urls) -> dict[str, str]:
        """``{url: fichero}`` de los logos que ya están (o se acaban de bajar).

        Las URLs que fallan no aparecen en el resultado: quien llama deja ese
        canal con su URL remota y la lista sigue sirviendo.
        """
        out: dict[str, str] = {}
        for url in urls:
            if not url:
                continue
            if url not in self._cache:
                self._cache[url] = self._ensure_one(url)
            nombre = self._cache[url]
            if nombre:
                out[url] = nombre
        return out

    # -- interno ---------------------------------------------------------------

    def _ensure_one(self, url: str) -> str | None:
        nombre = self._name_for(url)
        if not nombre:
            return None
        destino = self._dir / nombre
        try:
            if destino.is_file() and self._es_imagen(destino.read_bytes()[:16]):
                return nombre
        except OSError:
            # Fichero local ilegible/corrupto: se trata como ausente y se
            # vuelve a descargar más abajo.
            pass
        try:
            datos = self._opener(url)
        except Exception as e:  # noqa: BLE001 - un logo caído no es un fallo de la lista
            logger.warning("logo no descargado (%s): %s", url, e)
            return None
        if not isinstance(datos, (bytes, bytearray)) or not datos:
            logger.warning("logo vacío (%s)", url)
            return None
        if not self._es_imagen(bytes(datos[:16])):
            logger.warning("logo con formato desconocido (%s)", url)
            return None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            temporal = destino.with_name(destino.name + ".tmp")
            temporal.write_bytes(datos)
            temporal.replace(destino)
        except OSError as e:
            logger.warning("no se pudo guardar el logo %s: %s", nombre, e)
            return None
        return nombre

    def _download(self, url: str) -> bytes:
        peticion = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (weteve logo store)",
            # los CDN de Movistar sirven el logo sin esto
            "Referer": "https://www.movistarplus.es/programacion-tv",
        })
        with urllib.request.urlopen(peticion, timeout=self._timeout) as resp:
            return resp.read(MAX_BYTES)

    @staticmethod
    def _name_for(url: str) -> str | None:
        """Nombre de fichero estable y seguro a partir de la URL.

        Se conserva el nombre original (``TVE``, ``A3.png``) porque es legible
        y estable entre recargas; los CDN de Movistar no ponen extensión, así
        que se añade ``.png``. Solo si el nombre no es utilizable se recurre a
        un hash.
        """
        ruta = unquote(urlparse(str(url)).path)
        base = ruta.rsplit("/", 1)[-1].strip()
        if not base:
            return None
        limpio = re.sub(r"[^A-Za-z0-9._-]", "_", base)
        if limpio in ("", ".", "..") or len(limpio) > 80:
            import hashlib

            return hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:16] + ".png"
        if limpio.lower().endswith(_EXTENSIONES):
            return limpio
        if "." not in limpio:
            return limpio + ".png"
        import hashlib

        return hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:16] + ".png"

    @staticmethod
    def _es_imagen(cabecera: bytes) -> bool:
        return any(cabecera.startswith(firma) for firma in _FIRMAS)
