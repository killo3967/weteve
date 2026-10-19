"""El logo del mapeo tiene que verse: 4x el tamaño anterior.

Estaba en 16x16 px dentro de una columna de 56 px: se distinguía mal si el
canal estaba mapeado a la guía correcta.
"""

import pytest

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog


@pytest.fixture()
def cfg_file(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[listmanager.target.0]\nname = x\ncustom_m3u = /x.m3u\nport = 8080\n",
        encoding="utf-8")
    return cfg


@pytest.fixture()
def dialog(qtbot, cfg_file):
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg_file, "name": "x", "port": 8080,
    })
    qtbot.addWidget(d)
    d._config_watch.stop()
    return d


def test_columna_y_icono_del_logo_cuadruplicados(dialog):
    tabla = dialog.epg_mapping.table
    assert dialog.epg_mapping.LOGO_ICON_PX == 64
    assert tabla.columnWidth(dialog.epg_mapping.COL_LOGO) == 224
    assert tabla.iconSize().width() == 64
    assert tabla.iconSize().height() == 64


def test_el_alto_de_fila_deja_sitio_al_icono(dialog):
    tabla = dialog.epg_mapping.table
    assert tabla.verticalHeader().defaultSectionSize() >= 64
