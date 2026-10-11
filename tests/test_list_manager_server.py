"""Tests del servidor del gestor de listas.

Dos niveles:
  - Unitarios: routing vía ``route()`` (sin sockets) y ciclo de vida con
    sockets crudos.
  - E2E HTTP: peticiones reales con ``urllib`` contra el servidor levantado
    en un puerto efímero. Desde que se desactivaron las reglas residuales
    de Codex (bloqueo loopback) y el filtrado de Adguard, el loopback es
    estable en este equipo (ver memoria: env/loopback-firewall).
"""

import socket
import urllib.error
import urllib.request

import pytest

from src.infrastructure.web.playlist_server import (
    EPG_PATH,
    PLAYLIST_PATH,
    PlaylistServer,
    find_port_owner,
    port_in_use,
)


class TestRouting:
    def test_playlist_route(self):
        server = PlaylistServer()
        server.set_content("#EXTM3U\n", "<tv/>")
        assert server.route(PLAYLIST_PATH) == ("#EXTM3U\n", "audio/x-mpegurl")

    def test_epg_route(self):
        server = PlaylistServer()
        server.set_content("#EXTM3U\n", "<tv/>")
        assert server.route(EPG_PATH) == ("<tv/>", "application/xml")

    def test_trailing_slash_tolerado(self):
        server = PlaylistServer()
        server.set_content("#EXTM3U\n", "<tv/>")
        assert server.route(PLAYLIST_PATH + "/") == ("#EXTM3U\n", "audio/x-mpegurl")

    def test_ruta_desconocida(self):
        server = PlaylistServer()
        assert server.route("/nope") is None


class TestLifecycle:
    def test_stop_sin_start_no_falla(self):
        server = PlaylistServer(port=0)
        server.stop()  # no debe lanzar

    def test_is_running(self):
        server = PlaylistServer(port=0)
        assert server.is_running is False
        assert server.start() is True
        assert server.is_running is True
        server.stop()
        assert server.is_running is False

    def test_start_idempotente(self):
        server = PlaylistServer(port=0)
        try:
            assert server.start() is True
            assert server.start() is True  # segunda llamada no rompe
        finally:
            server.stop()

    def test_puerto_ocupado_devuelve_false(self):
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        server = PlaylistServer(port=port)
        try:
            assert server.start() is False
            assert server.is_running is False
        finally:
            blocker.close()

    def test_url_base(self):
        server = PlaylistServer(host="127.0.0.1", port=8099)
        assert server.url_base == "http://127.0.0.1:8099"


class TestHttpEndToEnd:
    """Peticiones HTTP reales contra el servidor en puerto efímero (port=0).

    Ojo: con port=0 ``url_base`` no refleja el puerto real asignado; los
    tests usan ``server._server.server_port`` tras el arranque.
    """

    def _start(self, playlist: str = "", epg: str = "") -> PlaylistServer:
        server = PlaylistServer(port=0)
        server.set_content(playlist, epg)
        assert server.start() is True
        return server

    def _url(self, server: PlaylistServer, path: str) -> str:
        port = server._server.server_port
        return f"http://127.0.0.1:{port}{path}"

    def test_get_playlist_m3u(self):
        m3u = "#EXTM3U\n#EXTINF:-1 tvg-id=\"a\" ,Canal A\nhttp://a/1\n"
        server = self._start(playlist=m3u, epg="<tv/>")
        try:
            with urllib.request.urlopen(self._url(server, PLAYLIST_PATH), timeout=5) as r:
                assert r.status == 200
                assert r.headers.get_content_type() == "audio/x-mpegurl"
                body = r.read().decode("utf-8")
            assert body == m3u
        finally:
            server.stop()

    def test_get_epg_xml(self):
        epg = "<?xml version=\"1.0\"?><tv><channel id=\"a\"/></tv>"
        server = self._start(playlist="#EXTM3U\n", epg=epg)
        try:
            with urllib.request.urlopen(self._url(server, EPG_PATH), timeout=5) as r:
                assert r.status == 200
                assert r.headers.get_content_type() == "application/xml"
                body = r.read().decode("utf-8")
            assert body == epg
        finally:
            server.stop()

    def test_ruta_desconocida_devuelve_404(self):
        server = self._start(playlist="x", epg="y")
        try:
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(self._url(server, "/no-existe"), timeout=5)
            assert excinfo.value.code == 404
        finally:
            server.stop()

    def test_contenido_actualizable_en_caliente(self):
        server = self._start(playlist="#EXTM3U v1\n", epg="<tv/>")
        try:
            with urllib.request.urlopen(self._url(server, PLAYLIST_PATH), timeout=5) as r:
                assert "v1" in r.read().decode("utf-8")
            server.set_content("#EXTM3U v2\n", "<tv/>")
            with urllib.request.urlopen(self._url(server, PLAYLIST_PATH), timeout=5) as r:
                assert "v2" in r.read().decode("utf-8")
        finally:
            server.stop()

    def test_content_length_correcto_con_acentos(self):
        # UTF-8 multicbyte: Content-Length debe contar bytes, no caracteres.
        m3u = "#EXTM3U\n#EXTINF:-1,Cañón Televisión Ñ\nhttp://a/1\n"
        server = self._start(playlist=m3u, epg="<tv/>")
        try:
            with urllib.request.urlopen(self._url(server, PLAYLIST_PATH), timeout=5) as r:
                body = r.read()
            assert len(body) == len(m3u.encode("utf-8"))
            assert "Cañón Televisión Ñ" in body.decode("utf-8")
        finally:
            server.stop()

    def test_server_stop_corta_las_peticiones(self):
        server = self._start(playlist="#EXTM3U\n", epg="<tv/>")
        url = self._url(server, PLAYLIST_PATH)
        server.stop()
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(url, timeout=2)


class TestBindRedLocal:
    """Acceso desde otros equipos: bind 0.0.0.0 y url_base navegable."""

    def test_url_base_con_bind_red_muestra_ip_real(self):
        server = PlaylistServer(host="0.0.0.0", port=8080)
        url = server.url_base
        assert url.startswith("http://")
        host = url.split("//")[1].split(":")[0]
        assert host != "0.0.0.0"
        assert host != "127.0.0.1"

    def test_url_base_con_bind_local_sin_cambios(self):
        server = PlaylistServer(host="127.0.0.1", port=8080)
        assert server.url_base == "http://127.0.0.1:8080"

    def test_bind_0_0_0_0_acepta_conexion_local(self):
        # 0.0.0.0 escucha en todas las interfaces, incluida la loopback.
        server = PlaylistServer(port=0)
        server.set_content("#EXTM3U red\n", "<tv/>")
        assert server.start() is True
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server._server.server_port}{PLAYLIST_PATH}",
                timeout=5,
            ) as r:
                assert r.status == 200
                assert "red" in r.read().decode("utf-8")
        finally:
            server.stop()


class TestPortDiagnostics:
    """Chequeo de puerto ocupado e identificación del proceso dueño."""

    def test_port_in_use_libre(self):
        # Puerto efímero reservado sin escuchar: libre para bind.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        assert port_in_use("127.0.0.1", port) is False

    def test_port_in_use_ocupado_por_otro_proceso(self):
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        try:
            assert port_in_use("127.0.0.1", port) is True
        finally:
            blocker.close()

    def test_find_port_owner_libre_devuelve_none(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        assert find_port_owner(port, "127.0.0.1") is None

    def test_find_port_owner_identifica_proceso(self):
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        try:
            owner = find_port_owner(port, "127.0.0.1")
            assert owner is not None
            # El proceso actual (python/pytest) está escuchando en ese puerto.
            assert "python" in owner.lower() or owner.startswith("PID")
        finally:
            blocker.close()
