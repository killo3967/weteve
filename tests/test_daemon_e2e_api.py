"""E2E de la API REST v1 contra un daemon real.

Daemon completo + target sirviendo (fixtures M3U/XMLTV), puertos efímeros y sin
red externa. Se usa el ``ApiClient`` real, no un doble.
"""

import json


def _names(body: dict) -> set[str]:
    return {c["name"] for c in body.get("channels", [])}


class TestLecturas:
    def test_health_y_status(self, api, daemon):
        assert api.request("GET", "/health") == (
            200, {"ok": True, "api": "v1"})
        code, body = api.status()
        assert code == 200
        target = body["targets"]["0"]
        assert target["running"] is True
        assert target["port"] == daemon.status()["port"]
        assert target["channel_count"] == 3

    def test_target_y_canales(self, api):
        code, body = api.get("/targets/0")
        assert code == 200 and body["target"]["name"] == "e2e"

        code, body = api.get("/targets/0/channels")
        assert code == 200 and body["total"] == 3
        assert _names(body) == {"La 1", "La 2", "Teledeporte"}

    def test_candidatos_a_mapeo(self, api):
        code, body = api.mapping_candidates("0")
        assert code == 200 and body["total"] == 2
        assert _names(body) == {"Canal XXX", "Canal Cine Plus"}
        fila = next(c for c in body["channels"]
                    if c["name"] == "Canal Cine Plus")
        assert fila["url"] == "http://stream.example.com/cineplus"
        assert fila["tvg_id"] == ""

    def test_catalogo_epg_y_grupos(self, api):
        code, body = api.epg_channels("0")
        assert code == 200 and body["total"] == 4
        assert {"es.la1", "es.la2"} <= {c["id"] for c in body["channels"]}
        # cada canal dice de qué guía viene: el nombre de la fuente en la
        # cartera (la migración de custom_epg la nombra por el fichero)
        canal = next(c for c in body["channels"] if c["id"] == "es.la1")
        assert canal["lists"] == ["sample"]

        code, body = api.groups("0")
        assert code == 200
        assert {"General", "Deportes", "Cine"} <= set(body["groups"])

    def test_preview_de_filtros(self, api):
        code, body = api.preview("0", ["group-title:Deportes:0:or"])
        assert code == 200
        assert body["total"] == 5 and body["channels"] == ["Teledeporte"]


class TestEscrituras:
    def test_lote_con_recarga_diferida(self, api):
        # tres cambios sin recargar
        assert api.set_settings("0", name="e2e-2", reload=False)[0] == 200
        assert api.put("/targets/0/rules",
                       {"rules": ["group-title:!{Cine}:0:and"],
                        "reload": False})[0] == 200
        assert api.put("/targets/0/disabled",
                       {"disabled_channels": [], "reload": False})[0] == 200

        # el fichero ya tiene los cambios aunque no se ha ejecutado nada
        assert api.get("/targets/0")[1]["target"]["name"] == "e2e-2"
        assert api.mapping_candidates("0")[1]["total"] == 2  # snapshot viejo

        code, metrics = api.run("0")
        assert code == 200 and metrics["filtered_count"] == 3
        # la regla deja fuera el canal de Cine: solo queda XXX pendiente
        assert _names(api.mapping_candidates("0")[1]) == {"Canal XXX"}

    def test_mapeo_rescata_un_canal(self, api):
        code, body = api.set_mappings("0", {
            "channel_url": "http://stream.example.com/cineplus",
            "epg_id": "es.la1"}, reload=False)
        assert code == 200 and body["errors"] == []

        code, metrics = api.run("0")
        assert metrics["filtered_count"] == 4
        assert "Canal Cine Plus" in _names(api.get("/targets/0/channels")[1])
        assert "Canal Cine Plus" not in _names(api.mapping_candidates("0")[1])

    def test_sources_round_trip(self, api, daemon):
        # la config del arnés es legacy: el demonio la migra al arrancar
        inicial = api.sources()[1]["sources"]
        assert {s["kind"] for s in inicial} == {"m3u", "epg"}

        from e2e_support import M3U

        code, body = api.set_sources([
            {"name": "lista", "kind": "m3u",
             "location": str(M3U).replace("\\", "/")},
            {"name": "guia", "kind": "epg", "location": "C:/tv.xml"},
        ])
        assert code == 200 and len(body["sources"]) == 2
        m3u_id = next(s["id"] for s in body["sources"] if s["kind"] == "m3u")

        code, body = api.set_target_sources("0", [m3u_id], [] + [])
        assert code == 200
        refs = body["target"]["source_refs"]
        assert [r["id"] for r in refs] == [m3u_id]

        # sin EPG en la cartera no se aplica la regla de servidor: sirven los 5
        assert api.run("0")[1]["filtered_count"] == 5


class TestMigracionAlArrancar:
    def test_la_config_legacy_se_convierte_en_cartera(self, api):
        """Fuente única (custom_m3u/custom_epg) → filas de cartera + selección."""
        sources = api.sources()[1]["sources"]
        kinds = {s["kind"] for s in sources}
        assert kinds == {"m3u", "epg"}
        refs = api.get("/targets/0")[1]["target"]["source_refs"]
        assert [r["kind"] if "kind" in r else r["name"] for r in refs]
        # el target ya apunta a la fila migrada
        assert len(refs) == 1


class TestErrores:
    def test_target_inexistente(self, api):
        assert api.get("/targets/9")[0] == 404
        assert api.mapping_candidates("9")[0] == 404

    def test_cuerpo_invalido(self, api):
        code, body = api.request("PUT", "/targets/0/rules", {"rules": "x"})
        assert code == 400 and "lista" in body["error"]

    def test_json_roto(self, api, daemon):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", daemon.api_port,
                                          timeout=10)
        try:
            conn.request("PUT", "/api/v1/targets/0/rules", body="{no json",
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.status == 400
            assert "JSON" in json.loads(resp.read())["error"]
        finally:
            conn.close()

    def test_metodo_no_soportado(self, api):
        assert api.request("DELETE", "/targets/0/rules")[0] == 405

    def test_ruta_desconocida(self, api):
        assert api.get("/nada")[0] in (404, 405)
