"""Persistencia de targets en ``daemon.toml``.

Comprueba que las selecciones de cartera y los mapeos sobreviven al guardado y
que un target de fuente única (``custom_m3u``/``custom_epg``) sigue cargando con
``load_config`` (el perfil que usa la UI).
"""

from pathlib import Path

from toml_helpers import source, target, write_daemon

from src.application.services.daemon.target_store import TargetStore
from src.application.services.list_manager.models import (
    FilterRule,
    PipelineTarget,
    SourceRef,
)
from src.infrastructure.client import config_toml
from src.infrastructure.client.persistencia import load_config


def _seed(path: Path) -> None:
    write_daemon(path, targets=[target(
        "0", name="test", custom_m3u="C:/l/platero.m3u",
        custom_epg="C:/e/TV.xml", epg_source=-1,
        rules=["group-title:Spain:0:and"], port=8080, bind="127.0.0.1")])


def _raw(path: Path) -> dict:
    return config_toml.load(path)["targets"][0]


def _save(path: Path, **kw) -> None:
    """Guardado real: ``TargetStore.save`` (el único escritor del fichero)."""
    store = TargetStore(path)
    store.load()
    target_obj = PipelineTarget(
        name=kw.get("name", "test"),
        source_index=kw.get("m3u", "C:/l/platero.m3u"),
        rules=kw.get("rules", ()),
        epg_overrides=dict(kw.get("overrides", {})),
        disabled_channels=tuple(kw.get("disabled", ())),
        port=kw.get("port", 8080),
        source_refs=tuple(kw.get("refs", ())),
        starting_number=kw.get("starting_number", 1),
    )
    store.save("0", target_obj, epg_path=kw.get("epg", "C:/e/TV.xml"),
               bind=kw.get("bind", "127.0.0.1"))


class TestLoadConfigLegacy:
    def test_target_antiguo_carga_sin_refs(self, tmp_path):
        path = tmp_path / "daemon.toml"
        _seed(path)
        cfg = load_config(path)
        t = cfg["targets"][0]
        assert t["custom_m3u"] == "C:/l/platero.m3u"
        assert t["m3u_refs"] == [] and t["epg_refs"] == []

    def test_selecciones_cargan_como_refs_con_location(self, tmp_path):
        path = tmp_path / "daemon.toml"
        write_daemon(path,
                     sources=[source("aa", "platero", "m3u",
                                     "http://s/l.m3u", refresh_min=30)],
                     targets=[target("0", m3u_sources=["aa"])])
        cfg = load_config(path)
        assert cfg["targets"][0]["m3u_refs"] == [
            SourceRef("aa", "platero", "http://s/l.m3u")
        ]


class TestGuardadoSelecciones:
    def test_save_persiste_y_conserva(self, tmp_path):
        path = tmp_path / "daemon.toml"
        write_daemon(path,
                     sources=[source("aa", "platero", "m3u", "C:/l.m3u"),
                              source("bb", "secunda", "m3u", "C:/s.m3u")],
                     targets=[target("0")])

        _save(path, refs=(SourceRef("aa", "platero", "C:/l.m3u"),
                          SourceRef("bb", "secunda", "C:/s.m3u")),
              rules=(FilterRule.parse("group-title:Spain:0:and"),))

        raw = _raw(path)
        assert raw["m3u_sources"] == ["aa", "bb"]
        # reglas y puerto se conservan (se guarda la definición completa)
        assert raw["rules"] == ["group-title:Spain:0:and"]
        assert raw["port"] == 8080

    def test_save_sin_refs_limpia_selecciones(self, tmp_path):
        path = tmp_path / "daemon.toml"
        write_daemon(path, sources=[source("aa")],
                     targets=[target("0", m3u_sources=["aa"])])

        _save(path)  # sin refs

        assert _raw(path)["m3u_sources"] == []


class TestOverridesDummy:
    """El id sintético de 'Dummy 1h' debe sobrevivir el guardado sin
    reinterpretarse: si el round-trip lo altera, el canal vuelve a quedarse
    sin guía y desaparece de la lista."""

    URL = ("http://proveedor.example:25461/usuario/clave/421"
           "?h=0123456789ab&z=&i=132511")
    DUMMY = "weteve-dummy-f7175fa991c8"

    def test_id_dummy_sobrevive_el_round_trip(self, tmp_path):
        path = tmp_path / "daemon.toml"
        _seed(path)
        _save(path,
              overrides={self.URL: self.DUMMY, "http://otro/canal": "La1.TV"},
              disabled=["http://apagado/a"])

        back = load_config(path)["targets"][0]
        # la url tiene ':', '?' y '&': en TOML va como clave entrecomillada
        assert back["epg_overrides"][self.URL] == self.DUMMY
        assert back["epg_overrides"]["http://otro/canal"] == "La1.TV"
        assert list(back["disabled_channels"]) == ["http://apagado/a"]

    def test_el_daemon_lee_el_mismo_id_dummy(self, tmp_path):
        """TargetStore y load_config leen con el mismo codec: si divergieran,
        el Dummy que la UI guardó no se serviría."""
        path = tmp_path / "daemon.toml"
        _seed(path)
        _save(path, overrides={self.URL: self.DUMMY})

        store = TargetStore(path)
        store.load()
        got = store.get("0")
        assert got is not None
        assert got.epg_overrides[self.URL] == self.DUMMY
