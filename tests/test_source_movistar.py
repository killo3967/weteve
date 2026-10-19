"""La guía de Movistar+ como fuente más de la cartera.

No es un fichero ni una URL de XMLTV: se declara con el esquema
``movistar://`` y la validación de la API tiene que aceptarla y conservarla al
guardar y al releer.
"""

from test_daemon_service import _StubSupervisor
from toml_helpers import target, write_daemon

from src.application.services.daemon.service import TargetService
from src.application.services.daemon.target_store import TargetStore
from src.application.services.list_manager.source_catalog import (
    infer_source_type,
)
from src.infrastructure.client import config_toml


def _service(tmp_path):
    path = tmp_path / "daemon.toml"
    write_daemon(path, sources=[],
                 targets=[target("0", name="t", custom_m3u="C:/x.m3u")])
    store = TargetStore(path)
    store.load()
    return TargetService(_StubSupervisor(), store), path


class TestTipoDeOrigenMovistar:
    def test_infer_source_type_reconoce_el_esquema(self):
        assert infer_source_type("movistar://epg") == "movistar"
        assert infer_source_type("MOVISTAR://epg?span=7") == "movistar"
        assert infer_source_type("https://x/y.xml.gz") == "url"
        assert infer_source_type("C:/datos/guia.xml") == "file"

    def test_set_sources_acepta_y_persiste_movistar(self, tmp_path):
        service, path = _service(tmp_path)
        salida = service.set_sources([{
            "name": "Guía Movistar+",
            "kind": "epg",
            "location": "movistar://epg?span=7",
            "refresh_min": 720,
        }])
        assert "error" not in salida, salida
        assert salida["sources"][0]["source_type"] == "movistar"
        assert "movistar://epg?span=7" in path.read_text(encoding="utf-8")

    def test_el_tipo_sobrevive_a_releer_la_config(self, tmp_path):
        service, path = _service(tmp_path)
        service.set_sources([{"name": "Guía Movistar+", "kind": "epg",
                              "location": "movistar://epg?span=7"}])
        store = TargetStore(path)
        store.load()
        entradas = config_toml.catalog_from(config_toml.load(path)).of_kind("epg")
        assert [e.source_type for e in entradas] == ["movistar"]
        assert [e.location for e in entradas] == ["movistar://epg?span=7"]

    def test_un_tipo_desconocido_no_se_inventa(self, tmp_path):
        """``source_type`` basura se deduce de la ubicación, no se rechaza."""
        service, _ = _service(tmp_path)
        salida = service.set_sources([{"name": "x", "kind": "epg",
                                       "location": "movistar://epg",
                                       "source_type": "loquesea"}])
        assert "error" not in salida, salida
        assert salida["sources"][0]["source_type"] == "movistar"
