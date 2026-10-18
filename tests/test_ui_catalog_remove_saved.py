"""Regresión: quitar una lista y guardar debe borrarla de config.ini.

Cada pestaña de la cartera reconstruye su tipo desde la tabla y conserva el
otro tipo tal como estaba en su copia de la cartera. Al guardar, el diálogo
mezclaba ambos resultados: la copia de «Listas EPG» resucitaba las filas M3U
que el usuario acababa de quitar, así que seguían ahí tras reiniciar.
"""

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from src.application.services.list_manager.models import SourceRef
from src.infrastructure.client.sources_store import read_catalog
from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

CONFIG = """[listmanager.target.0]
name = test
custom_m3u = C:/platero.m3u
custom_epg =
port = 9999
bind = 127.0.0.1
rules =
epg_overrides =
disabled_channels =
m3u_sources = aaa
epg_sources =

[source.aaa]
name = platero2026
kind = m3u
source_type = file
location = C:/platero.m3u
refresh_min = 0
enabled = 1

[source.bbb]
name = abilio
kind = m3u
source_type = url
location = http://
refresh_min = 0
enabled = 1
"""


def _catalog(path: Path):
    import configparser

    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    return read_catalog(parser)


@pytest.fixture
def dialog(qtbot, tmp_path, monkeypatch):
    from src.infrastructure.adapters import file_m3u_repository as repo

    monkeypatch.setattr(repo.FileM3URepository, "_get_content",
                        lambda self, source: ["#EXTM3U"])
    path = tmp_path / "config.ini"
    path.write_text(CONFIG, encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": path, "name": "test",
        "custom_m3u": "C:/platero.m3u", "port": 9999,
        "m3u_refs": [SourceRef("aaa", "platero2026", "C:/platero.m3u")],
    })
    qtbot.addWidget(d)
    d._config_watch.stop()  # el refresco desde disco no participa aquí
    return d


class TestQuitarListaYGuardar:
    def test_la_fila_quitada_no_viaja_al_demonio(self, dialog, fake_api):
        """Lo que se manda es la cartera visible: la quitada no aparece.

        El fichero lo escribe el demonio con ese payload, así que este es el
        invariante real (antes se comprobaba el fichero, que escribía la GUI).
        """
        w = dialog.catalog_m3u_widget
        assert w.table.rowCount() == 2
        w.table.selectRow(1)  # "abilio"
        w.remove_selected()
        dialog._on_save()
        enviado = [c for c in fake_api.calls if c[0] == "sources"][-1]
        names = [e["name"] for e in enviado[1] if e["kind"] == "m3u"]
        assert names == ["platero2026"]

    def test_la_lista_activa_sigue_seleccionada(self, dialog, fake_api):
        """Quitar otra fila no puede deseleccionar la lista del target."""
        w = dialog.catalog_m3u_widget
        w.table.selectRow(1)
        w.remove_selected()
        dialog._on_save()
        enviado = [c for c in fake_api.calls if c[0] == "target_sources"][-1]
        assert enviado[2] == ["aaa"]

    def test_quitar_todas_las_listas_las_borra(self, dialog, fake_api):
        w = dialog.catalog_m3u_widget
        w.table.selectAll()
        w.remove_selected()
        dialog._on_save()
        enviado = [c for c in fake_api.calls if c[0] == "sources"][-1]
        assert [e["name"] for e in enviado[1] if e["kind"] == "m3u"] == []

    def test_las_listas_epg_no_se_tocan(self, dialog, fake_api):
        dialog.catalog_m3u_widget.table.selectRow(1)
        dialog.catalog_m3u_widget.remove_selected()
        dialog._on_save()
        enviado = [c for c in fake_api.calls if c[0] == "sources"][-1]
        assert [e["name"] for e in enviado[1] if e["kind"] == "epg"] == []
