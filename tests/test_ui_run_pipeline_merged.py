"""Diálogo: _run_pipeline registra la fuente fusionada en modo multi-fuente.

Bug: con filas activas en la cartera el target usaba source_index="merged"
pero el diálogo solo llamaba register_source → "Fuente merged no registrada".
"""

import pytest

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

pytest.importorskip("PyQt6")


@pytest.fixture
def dialog(qtbot, tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[DAEMON]\ncontrol_port = 32123\n\n[listmanager.target.0]\n"
        "name = x\nport = 8080\n",
        encoding="utf-8",
    )
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x", "port": 8080,
    })
    qtbot.addWidget(d)
    return d


class TestRunPipelineMerged:
    def test_fuente_merged_registrada(self, dialog, monkeypatch):
        dialog.catalog_m3u_widget.add_row()
        w = dialog.catalog_m3u_widget
        w.table.item(0, 0).setText("local")
        w.table.item(0, 2).setText("http://s/l.m3u")

        registered = {}
        real = dialog._pipeline.register_merged

        def spy(index, source_refs, epg_refs):
            registered["called"] = True
            return real(index, source_refs, epg_refs)

        monkeypatch.setattr(dialog._pipeline, "register_merged", spy)
        dialog._run_pipeline()
        assert registered.get("called")

    def _patch_download(self, dialog, monkeypatch, channels):
        """Cualquier descarga/carga de fuente devuelve una lista m3u fija."""
        from src.infrastructure.adapters import file_m3u_repository as repo
        monkeypatch.setattr(
            repo.FileM3URepository, "_get_content",
            lambda self, source: [line for i, n in enumerate(channels)
                                  for line in (
                                      f'#EXTINF:-1 tvg-id="c{i}" group-title="G",{n}',
                                      f"http://st/{i}")])

    def test_pipeline_completo_en_modo_merged(self, dialog, monkeypatch):
        self._patch_download(dialog, monkeypatch, ["Canal A", "Canal B"])
        dialog.catalog_m3u_widget.add_row()
        w = dialog.catalog_m3u_widget
        w.table.item(0, 0).setText("local")
        w.table.item(0, 2).setText("http://s/l.m3u")
        result = dialog._run_pipeline()
        assert result is not None
        assert result.total_input == 2

    def test_flujo_legacy_intacto(self, dialog, monkeypatch):
        from src.infrastructure.adapters import file_m3u_repository as repo
        monkeypatch.setattr(
            repo.FileM3URepository, "_get_content",
            lambda self, source: ["#EXTM3U", '#EXTINF:-1 group-title="G",X', "http://st/1"])
        dialog.custom_m3u_edit.setText("C:/legacy.m3u")
        result = dialog._run_pipeline()
        assert result is not None
        assert result.total_input == 1
