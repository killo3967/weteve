"""Regresión: la tabla de mapeo debe seguir mostrando los canales descartados.

Desde 2026-10-14 el servidor descarta todo canal sin guía EPG cuando hay fuente
(``pipeline`` -> ``excluded_no_epg``). Esos canales **no** están en
``result.channels``, así que si el diálogo solo leyera los canales servidos, la
pestaña 'Mapeado EPG' se quedaría vacía justo cuando más la necesitas: el
usuario no vería qué canales han caído ni podría asignarles un EPG real o
'Dummy 1h' para rescatarlos.
"""

from pathlib import Path

import pytest

from src.application.services.list_manager.models import MappedChannel
from src.application.services.list_manager.pipeline import PipelineResult
from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

M3U = """#EXTM3U
#EXTINF:-1 tvg-id="La1.TV",La 1 HD
http://x/la1.ts
#EXTINF:-1 tvg-id="otro",Antena 3 Internacional
http://x/a3int.ts
"""


@pytest.fixture
def dialog(qtbot, tmp_path):
    m3u = tmp_path / "lista.m3u"
    m3u.write_text(M3U, encoding="utf-8")
    cfg = tmp_path / "config.ini"
    cfg.write_text("[DAEMON]\ncontrol_port = 32123\n", encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x",
        "custom_m3u": str(m3u).replace("\\", "/"), "rules": [], "port": 8080,
    })
    qtbot.addWidget(d)
    return d


def _result(**kw) -> PipelineResult:
    defaults = dict(
        channels=[MappedChannel("La 1 HD", "http://x/la1.ts",
                                 matched_epg_id="La1.TV")],
        total_input=2,
        unmatched=["Antena 3 Internacional"],
        excluded_no_epg=[MappedChannel("Antena 3 Internacional",
                                       "http://x/a3int.ts")],
        require_epg=True,
    )
    defaults.update(kw)
    return PipelineResult(**defaults)


class TestFilasDeLaTabla:
    def test_los_descartados_sin_guia_siguen_en_la_tabla(self, dialog, monkeypatch):
        """Es la vía de rescate: sin fila no hay desplegable que asignar."""
        monkeypatch.setattr(dialog, "_run_pipeline", lambda: _result())
        dialog._refresh_mapping()
        rows = [dialog.epg_mapping.table.item(r, 0).text()
                for r in range(dialog.epg_mapping.table.rowCount())]
        assert rows == ["Antena 3 Internacional"]

    def test_un_canal_desactivado_a_mano_sigue_listado(self, dialog, monkeypatch):
        """Necesario para poder reactivarlo; no lo cambia la regla de servidor."""
        monkeypatch.setattr(dialog, "_run_pipeline", lambda: _result(
            excluded_no_epg=[],
            excluded_disabled=[MappedChannel("La 1 HD", "http://x/la1.ts",
                                             matched_epg_id="La1.TV")],
        ))
        dialog._refresh_mapping()
        assert dialog.epg_mapping.table.rowCount() == 1

    def test_descartados_y_desactivados_no_se_repiten(self, dialog, monkeypatch):
        """Un canal puede estar en ambas listas (apagado y sin guía). Ver que
        no se dupliquen no es cosmético: dos filas para la misma url hacen que
        get_overrides() guarde la última y el usuario no sepa qué edita."""
        ch = MappedChannel("Antena 3 Internacional", "http://x/a3int.ts")
        monkeypatch.setattr(dialog, "_run_pipeline", lambda: _result(
            excluded_no_epg=[ch], excluded_disabled=[ch],
        ))
        dialog._refresh_mapping()
        urls = [dialog.epg_mapping.table.item(r, 1).text()
                for r in range(dialog.epg_mapping.table.rowCount())]
        assert urls == ["http://x/a3int.ts"]

    def test_un_canal_servido_sin_match_real_tambien_se_lista(self, dialog, monkeypatch):
        """Con fuente EPG esto no debería pasar, pero si el emparejador cambia
        la tabla no puede dejar fuera un canal sin guía asignada."""
        monkeypatch.setattr(dialog, "_run_pipeline", lambda: _result(
            channels=[MappedChannel("Sin Guia", "http://x/sg")],
            excluded_no_epg=[], require_epg=False, unmatched=["Sin Guia"],
        ))
        dialog._refresh_mapping()
        assert dialog.epg_mapping.table.rowCount() == 1


class TestResumen:
    def test_el_resumen_anuncia_los_descartados(self, dialog, monkeypatch):
        """285 canales -> 1 servido es un cambio brutal: tiene que ser visible
        y decir por qué, o parece que la lista está rota."""
        monkeypatch.setattr(dialog, "_run_pipeline", lambda: _result())
        dialog._refresh_mapping()
        text = dialog.result_label.text()
        assert "1 sin guía EPG" in text
        assert "descartados" in text

    def test_sin_fuente_epg_no_se_anuncia_descarte(self, dialog, monkeypatch):
        """Sin guía no hay regla: decir 'descartados' sería mentira y haría
        buscar al usuario un problema que no existe."""
        monkeypatch.setattr(dialog, "_run_pipeline", lambda: _result(
            channels=[MappedChannel("La 1 HD", "http://x/la1.ts")],
            excluded_no_epg=[], require_epg=False, unmatched=["La 1 HD"],
        ))
        dialog._refresh_mapping()
        assert "descartados" not in dialog.result_label.text()

    def test_cero_descartados_no_anuncia_descartes(self, dialog, monkeypatch):
        """Con fuente EPG y todo asignado, '0 descartados' invitaría a buscar un
        problema que no hay; el resumen solo habla de descartes si los hubo."""
        monkeypatch.setattr(dialog, "_run_pipeline", lambda: _result(
            excluded_no_epg=[], unmatched=[],
        ))
        dialog._refresh_mapping()
        assert "descartados" not in dialog.result_label.text()
