"""Pestaña «Estado»: semáforo del demonio y URLs de cada servicio."""

import pytest
from fakes_api import FakeApi

from src.infrastructure.ui.status_panel import GREEN, RED, StatusPanel

pytest.importorskip("PyQt6")


def _panel(qtbot, api) -> StatusPanel:
    panel = StatusPanel(client_factory=lambda: api)
    qtbot.addWidget(panel)
    panel.refresh()
    return panel


def _urls(panel: StatusPanel) -> list[str]:
    out = []
    for row in range(panel.services_form.rowCount()):
        item = panel.services_form.itemAt(
            row, panel.services_form.ItemRole.FieldRole)
        if item is not None and item.widget() is not None:
            out.append(item.widget().text())
    return out


class TestSemaforo:
    def test_demonio_en_marcha_verde(self, qtbot):
        panel = _panel(qtbot, FakeApi())
        assert panel.dot.text() == GREEN
        assert "en marcha" in panel.state_label.text()

    def test_demonio_parado_rojo(self, qtbot):
        panel = _panel(qtbot, FakeApi(down=True))
        assert panel.dot.text() == RED
        assert "no responde" in panel.state_label.text()

    def test_lista_los_targets_y_su_estado(self, qtbot):
        api = FakeApi()
        panel = _panel(qtbot, api)
        textos = [w.text() for w in panel.targets_group.findChildren(
            type(panel.state_label))]
        assert any("Perfil 0" in t and "sirviendo" in t for t in textos), textos


class TestUrls:
    def test_un_solo_puerto_con_cada_servicio(self, qtbot):
        panel = _panel(qtbot, FakeApi())
        urls = _urls(panel)
        assert "http://127.0.0.1:34400/health" in urls
        assert "http://127.0.0.1:34400/api/v1" in urls
        assert "http://127.0.0.1:34400/mcp" in urls
        assert "http://127.0.0.1:34400/m3u/weteve_0.m3u" in urls
        assert "http://127.0.0.1:34400/xmltv/weteve_0.xml" in urls

    def test_sin_demonio_las_urls_siguen_a_la_vista(self, qtbot):
        panel = _panel(qtbot, FakeApi(down=True))
        assert any("/mcp" in u for u in _urls(panel))


class TestPestana:
    def test_es_la_primera(self, qtbot, tmp_path):
        from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

        cfg = tmp_path / "config.ini"
        cfg.write_text("[DAEMON]\ncontrol_port = 34400\n", encoding="utf-8")
        d = ListManagerDialog(None, config={
            "target_id": "0", "_config_path": cfg, "name": "t"})
        qtbot.addWidget(d)
        d._config_watch.stop()
        assert d.tabs.tabText(0) == "Estado"
        assert d.tabs.currentIndex() == 0


class TestStreamsDelProveedor:
    """«N de M streams responden»: aviso barato de que el origen se cayó."""

    def _textos(self, panel: StatusPanel) -> list[str]:
        return [w.text() for w in panel.targets_group.findChildren(
            type(panel.state_label))]

    def test_dice_cuantos_responden(self, qtbot):
        api = FakeApi()
        api.streams = {"checked": 3, "ok": 2, "pending": False}
        panel = _panel(qtbot, api)
        assert any("2 de 3 streams responden" in t
                   for t in self._textos(panel)), self._textos(panel)

    def test_avisa_si_baja_del_umbral(self, qtbot):
        api = FakeApi()
        api.streams = {"checked": 3, "ok": 0, "pending": False}
        panel = _panel(qtbot, api)
        textos = self._textos(panel)
        assert any("0 de 3 streams responden" in t and "proveedor" in t
                   for t in textos), textos

    def test_sin_muestras_no_dice_nada(self, qtbot):
        panel = _panel(qtbot, FakeApi())
        assert not any("streams responden" in t for t in self._textos(panel))
