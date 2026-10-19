"""Tests de conectividad real: descarga de fuentes URL y validación de formato.

Marcados ``network`` y EXCLUIDOS por defecto (pytest.ini: addopts
``-m "not network"``). Se ejecutan explícitamente con::

    python -m pytest tests/test_network_sources.py -m network -v

Leen la cartera real de ``daemon.toml`` (``[[sources]]`` de tipo url).
Si no hay ninguna fuente URL de un kind, ese bloque se salta (SKIP), así el
test nunca falla por configuración sino por red/formato.
"""

from __future__ import annotations

import gzip
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import requests

from src.infrastructure.client.sources_store import load_catalog

REPO = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("WETEVE_NETWORK_CONFIG",
                             str(REPO / "daemon.toml")))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
TIMEOUT = 20
MAX_BYTES = 20 * 1024 * 1024  # guard: no arrastrar >20MB a memoria en tests


def _url_sources(kind: str) -> list:
    # Se llama desde `parametrize` al importar: no puede saltar, o la
    # recolección falla. Sin fichero devuelve vacío y no se generan casos.
    if not CONFIG.exists():
        return []
    catalog = load_catalog(CONFIG)
    return [e for e in catalog.of_kind(kind, enabled_only=True)
            if e.source_type == "url"]



def _download(url: str, attempts: int = 5) -> bytes:
    """Descarga con reintentos (flake Windows de RST en loopback inmediato,
    mismo patrón que ``_urlopen_retry`` en test_list_manager_server.py)."""
    data = b""
    for i in range(attempts):
        try:
            t0 = time.monotonic()
            resp = requests.get(url, headers={"User-Agent": UA},
                                timeout=TIMEOUT, stream=True)
            resp.raise_for_status()
            data = b""
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                data += chunk
                if len(data) > MAX_BYTES:
                    pytest.fail(
                        f"{url}: respuesta > {MAX_BYTES // (1024 * 1024)} MB")
            break
        except requests.exceptions.RequestException:
            if i == attempts - 1:
                raise
            time.sleep(0.2 * (i + 1))
    elapsed = time.monotonic() - t0
    assert len(data) > 0, f"{url}: respuesta vacía"
    print(f"    {url}: {len(data) / 1024:.1f} KB en {elapsed:.2f}s "
          f"(HTTP {resp.status_code})")
    return data


class TestM3UNetwork:
    @pytest.mark.network
    def test_hay_fuentes_url_m3u(self):
        # Documenta cuántas hay; si es 0, los demás tests saltan solos.
        sources = _url_sources("m3u")
        print(f"    fuentes m3u url: {[e.location for e in sources]}")

    @pytest.mark.network
    @pytest.mark.parametrize("entry", _url_sources("m3u"), ids=lambda e: e.id)
    def test_m3u_descarga_y_formato(self, entry):
        data = _download(entry.location)
        text = data.decode("utf-8", errors="replace")
        lines = [l for l in text.splitlines() if l.strip()]
        # Formato M3U: cabecera EXTM3U y al menos un EXTINF con URL.
        assert lines[0].startswith("#EXTM3U"), (
            f"{entry.location}: primera línea no es #EXTM3U: {lines[0][:80]!r}")
        extinfs = [l for l in lines if l.startswith("#EXTINF")]
        assert extinfs, f"{entry.location}: sin #EXTINF"
        urls = [l for l in lines
                if not l.startswith("#") and l.startswith(("http", "rtmp"))]
        assert urls, f"{entry.location}: sin URLs de canal"
        # Coherencia básica: cada EXTINF suele ir seguido de su URL.
        assert len(extinfs) <= len(urls) * 2, (
            f"{entry.location}: {len(extinfs)} EXTINF vs {len(urls)} URLs — "
            "formato sospechoso")


class TestEpgNetwork:
    @pytest.mark.network
    @pytest.mark.parametrize("entry", _url_sources("epg"), ids=lambda e: e.id)
    def test_epg_descarga_y_formato(self, entry):
        data = _download(entry.location)
        # Posibles envoltorios: gzip (xml.gz) o xml directo.
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        text = data.decode("utf-8", errors="replace")
        # XML bien formado: parsear sin explotar.
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            pytest.fail(f"{entry.location}: XMLTV mal formado: {e}")
        assert root.tag == "tv", (
            f"{entry.location}: raíz inesperada <{root.tag}>, esperada <tv>")
        channels = root.findall("channel")
        programmes = root.findall("programme")
        assert channels, f"{entry.location}: XMLTV sin canales"
        print(f"    {entry.location}: {len(channels)} canales, "
              f"{len(programmes)} programas")

    @pytest.mark.network
    @pytest.mark.parametrize("entry", _url_sources("epg"), ids=lambda e: e.id)
    def test_epg_programacion_no_vacia(self, entry):
        """El EPG no solo es válido: tiene programas con fechas futuras."""
        data = _download(entry.location)
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        root = ET.fromstring(data.decode("utf-8", errors="replace"))
        programmes = root.findall("programme")
        assert programmes, f"{entry.location}: sin <programme>"
        # Al menos un programa tiene start parseable.
        starts = [p.get("start", "") for p in programmes[:50]]
        assert any(len(s) >= 8 for s in starts), (
            f"{entry.location}: atributos start ausentes o corruptos")
