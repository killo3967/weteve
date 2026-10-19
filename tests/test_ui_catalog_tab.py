"""«Catálogo de la guía»: dial, canal, código interno y si está en la lista.

Es la tabla de referencia del proveedor. Existe porque el código de cadena
sobrevive al cambio de marca (``BBDRA`` era «BBC Drama» y hoy es «BBC Series»)
y sin verlo no hay forma de encontrar el canal en el desplegable.
"""

import pytest

from src.application.services.daemon.supervisor import TargetSupervisor
from src.infrastructure.ui.list_manager_dialog import CatalogWidget

FILAS = [
    {"id": "TVE", "name": "LA 1", "code": "TVE", "dial": 1,
     "logo": "http://l/tve.png", "lists": ["Guía Movistar+"], "served": True},
    {"id": "BBDRA", "name": "BBC Series", "code": "BBDRA", "dial": 32,
     "logo": "", "lists": ["Guía Movistar+"], "served": False},
    {"id": "UCL", "name": "UCL", "code": "UCL", "dial": 143,
     "logo": "", "lists": ["Doble_M", "Guía Movistar+"], "served": False},
]


class TestWidgetDelCatalogo:
    @pytest.fixture()
    def widget(self, qtbot):
        w = CatalogWidget()
        qtbot.addWidget(w)
        w.set_channels(FILAS)
        return w

    def test_columnas(self, widget):
        cabeceras = [widget.table.horizontalHeaderItem(col).text()
                     for col in range(widget.table.columnCount())]
        assert cabeceras == ["Dial", "Canal", "Código", "Guías", "En tu lista"]

    def test_pinta_dial_nombre_y_codigo(self, widget):
        assert widget.table.item(0, widget.COL_DIAL).text() == "1"
        assert widget.table.item(1, widget.COL_NAME).text() == "BBC Series"
        assert widget.table.item(1, widget.COL_CODE).text() == "BBDRA"

    def test_marca_los_que_estan_en_la_lista(self, widget):
        assert widget.table.item(0, widget.COL_SERVED).text() == "sí"
        assert widget.table.item(1, widget.COL_SERVED).text() == "no"

    def test_resumen(self, widget):
        assert "3 canales" in widget.summary.text()
        assert "1 en tu lista" in widget.summary.text()

    def test_un_canal_sin_dial_deja_la_celda_vacia(self, qtbot):
        w = CatalogWidget()
        qtbot.addWidget(w)
        w.set_channels([{"id": "X", "name": "X", "code": "", "dial": None,
                         "lists": [], "served": False}])
        assert w.table.item(0, w.COL_DIAL).text() == ""

    def test_filtra_por_nombre_y_por_codigo(self, widget):
        widget.name_filter.setText("drama")      # el nombre viejo no está
        assert widget.visible_rows() == 0
        widget.name_filter.setText("bbdra")      # el código sí
        assert widget.visible_rows() == 1
        widget.name_filter.setText("la 1")
        assert widget.visible_rows() == 1

    def test_borrar_el_filtro_muestra_todo(self, widget):
        widget.name_filter.setText("ucl")
        assert widget.visible_rows() == 1
        widget.name_filter.setText("")
        assert widget.visible_rows() == len(FILAS)


class TestEndpointsDelCatalogo:
    def _supervisor(self, tmp_path):

        from datetime import datetime

        from src.application.services.list_manager.models import PipelineTarget
        from src.domain.entities.channel import Channel
        from src.domain.entities.epg import ChannelInfo, EPGData, Program

        class _M3U:
            def get_channels(self, source):
                return [Channel("LA 1", "http://s/la1", "General", tvg_id="TVE")]

        class _Epg:
            def load_epg(self, source, fresh=False):
                return EPGData(
                    programs=[Program("P", datetime(2026, 10, 19, 12),
                                      datetime(2026, 10, 19, 13), "TVE")],
                    channel_names={"TVE": "LA 1", "BBDRA": "BBC Series"},
                    channel_info={"TVE": ChannelInfo(dial=1, logo="http://l/1.png"),
                                  "BBDRA": ChannelInfo(dial=32)},
                )

        from src.application.services.list_manager.pipeline import ListManagerPipeline

        m3u = tmp_path / "l.m3u"
        m3u.write_text("#EXTM3U", encoding="utf-8")
        sup = TargetSupervisor()
        sup._pipeline = ListManagerPipeline(m3u_repository=_M3U(),
                                            epg_repository=_Epg())
        sup.start_target("0", PipelineTarget(name="t", source_index=0),
                        "guia.xml")
        return sup

    def test_devuelve_dial_codigo_y_estado(self, tmp_path):
        sup = self._supervisor(tmp_path)
        body = sup.epg_catalog("0")
        filas = {f["id"]: f for f in body["channels"]}
        assert filas["TVE"]["dial"] == 1
        assert filas["TVE"]["served"] is True
        assert filas["BBDRA"]["code"] == "BBDRA"
        assert filas["BBDRA"]["served"] is False

    def test_ordenado_por_dial(self, tmp_path):
        sup = self._supervisor(tmp_path)
        diales = [f["dial"] for f in sup.epg_catalog("0")["channels"]]
        assert diales == sorted(d for d in diales if d is not None) + \
            [d for d in diales if d is None]
