"""E2E del servidor de canales del target (playlist.m3u + epg.xml).

El servidor lo levanta el demonio al arrancar el target; aquí se pide por HTTP
real, como haría un reproductor IPTV.
"""


class TestServidorDePlaylist:
    def test_sirve_la_playlist(self, daemon):
        status, body = daemon.fetch("/playlist.m3u")
        assert status == 200
        assert body.startswith("#EXTM3U")
        assert ",La 1" in body
        assert "http://stream.example.com/la1" in body
        # el enlace con la guía va por id; el <display-name> sigue ahí para
        # quien empareje por nombre (ver build_m3u)
        assert 'tvg-id="es.la1"' in body
        assert 'tvg-id="es.tdp"' in body      # casó por nombre con el XMLTV

    def test_sirve_el_epg(self, daemon):
        status, body = daemon.fetch("/epg.xml")
        assert status == 200
        assert "<tv" in body and "<programme" in body
        assert "La 1" in body

    def test_la_url_y_el_puerto_son_los_del_target(self, daemon, api):
        url = daemon.playlist_url()
        assert url.endswith(".m3u") and "/m3u/weteve_" in url
        puerto = int(url.rsplit(":", 1)[1].split("/")[0])
        assert puerto == daemon.status()["port"]
        code, body = api.get("/targets/0/status")
        assert code == 200 and body["port"] == puerto

    def test_ruta_desconocida_404(self, daemon):
        assert daemon.fetch("/nada")[0] == 404

    def test_cambia_cuando_el_demonio_recarga(self, daemon, api):
        """Desactivar un canal en la API se refleja en lo que se sirve."""
        assert ",La 1" in daemon.fetch("/playlist.m3u")[1]

        api.put("/targets/0/disabled",
                {"disabled_channels": ["http://stream.example.com/la1"],
                 "reload": False})
        assert api.run("0")[1]["filtered_count"] == 2

        body = daemon.fetch("/playlist.m3u")[1]
        assert ",La 1" not in body
        assert ",La 2" in body
        assert ",Teledeporte" in body
        # y el EPG servido tampoco lo anuncia
        assert "es.la1" not in daemon.fetch("/epg.xml")[1]
