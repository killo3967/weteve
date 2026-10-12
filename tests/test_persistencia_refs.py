"""Persistencia de targets vía load_config/save_config (formato main.py/UI).

Comprueba que las selecciones de cartera (m3u_refs/epg_refs) sobreviven al
guardado y que las configs antiguas de fuente única siguen cargando.
"""

import configparser
from pathlib import Path

from src.application.services.list_manager.models import SourceRef
from src.infrastructure.client.persistencia import load_config, save_config


def _seed(path: Path) -> None:
    parser = configparser.ConfigParser()
    parser["listmanager.target.0"] = {
        "name": "test",
        "custom_m3u": "C:/l/platero.m3u",
        "custom_epg": "C:/e/TV.xml",
        "epg_source": "-1",
        "rules": "group-title:Spain:0:and",
        "epg_overrides": "",
        "port": "8080",
        "bind": "127.0.0.1",
    }
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)


class TestLoadConfigLegacy:
    def test_target_antiguo_carga_sin_refs(self, tmp_path):
        path = tmp_path / "config.ini"
        _seed(path)
        cfg = load_config(path)
        t = cfg["targets"][0]
        assert t["custom_m3u"] == "C:/l/platero.m3u"
        assert t["m3u_refs"] == [] and t["epg_refs"] == []

    def test_selecciones_cargan_como_refs_con_location(self, tmp_path):
        path = tmp_path / "config.ini"
        _seed(path)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        parser["source.aa"] = {"name": "platero", "kind": "m3u",
                               "source_type": "url", "location": "http://s/l.m3u",
                               "refresh_min": "30", "enabled": "1"}
        parser["listmanager.target.0"]["m3u_sources"] = "aa"
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)
        cfg = load_config(path)
        assert cfg["targets"][0]["m3u_refs"] == [
            SourceRef("aa", "platero", "http://s/l.m3u")
        ]


class TestSaveConfigSelecciones:
    def test_save_persiste_y_conserva(self, tmp_path):
        path = tmp_path / "config.ini"
        _seed(path)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        parser["source.aa"] = {"name": "platero", "kind": "m3u",
                               "source_type": "file", "location": "C:/l.m3u",
                               "refresh_min": "0", "enabled": "1"}
        parser["source.bb"] = {"name": "secunda", "kind": "m3u",
                               "source_type": "file", "location": "C:/s.m3u",
                               "refresh_min": "0", "enabled": "1"}
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)

        cfg = load_config(path)
        cfg["targets"][0]["m3u_refs"] = [
            SourceRef("aa", "platero", "C:/l.m3u"),
            SourceRef("bb", "secunda", "C:/s.m3u"),
        ]
        save_config(path, cfg)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        assert parser.get("listmanager.target.0", "m3u_sources") == "aa,bb"
        # reglas y puerto intactos tras el round-trip
        assert "group-title:Spain:0:and" in parser.get("listmanager.target.0", "rules")
        assert parser.getint("listmanager.target.0", "port") == 8080

    def test_save_sin_refs_limpia_selecciones(self, tmp_path):
        path = tmp_path / "config.ini"
        _seed(path)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        parser["listmanager.target.0"]["m3u_sources"] = "aa"
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)
        save_config(path, load_config(path))
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        assert parser.get("listmanager.target.0", "m3u_sources", fallback="") == ""
