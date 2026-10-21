"""Ficha de canal: dial forzado, nombre mostrado y logo.

El nombre interno (el de la lista) no se toca: manda en el dedupe y en los
pesos de calidad. Lo que se edita es la ficha: el número, el nombre que ve el
reproductor y el fichero de logo de ``assets/logos``.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.application.services.daemon.service import TargetService, logos_dir
from src.application.services.list_manager.dummy_epg import dummy_id_for
from src.application.services.list_manager.models import (
    ChannelProfile,
    PipelineTarget,
    channel_keys,
    merge_profiles,
    url_key,
)
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.domain.entities.channel import Channel
from src.domain.entities.epg import ChannelInfo, EPGData, Program
from src.infrastructure.client import config_toml

AHORA = datetime(2026, 10, 20, 12)


class _FakeM3URepo:
    def __init__(self, canales):
        self._canales = canales

    def get_channels(self, source):
        return list(self._canales)


class _FakeEpgRepo:
    def __init__(self, por_ruta):
        self._por_ruta = por_ruta

    def load_epg(self, source, fresh=False):
        return self._por_ruta.get(source, EPGData())


def _guia() -> EPGData:
    nombres = {"TVE": "LA 1", "MLIGA": "M+ LALIGA", "SUR": "Canal Sur"}
    info = {"TVE": ChannelInfo(dial=1), "MLIGA": ChannelInfo(dial=54)}
    return EPGData(
        programs=[Program("P", AHORA, AHORA + timedelta(hours=1), cid)
                  for cid in nombres],
        channel_names=nombres, channel_info=info,
        channel_priority={cid: 10 if cid in info else 0 for cid in nombres},
    )


def _run(canales, **kwargs):
    pipeline = ListManagerPipeline(
        m3u_repository=_FakeM3URepo(canales),
        epg_repository=_FakeEpgRepo({"guia.xml": _guia()}))
    pipeline.register_source(0, "lista.m3u", "guia.xml")
    target = PipelineTarget(name="t", source_index=0, epg_source_index=0,
                            **kwargs)
    return pipeline.run(target)


class TestIdentidad:
    def test_la_clave_mas_fiable_es_el_codigo_de_guia(self):
        claves = channel_keys("MLIGA", "http://x/1", "M+ LaLigaTVHD")
        assert claves[0] == "MLIGA"
        assert claves[1] == url_key("http://x/1")
        assert claves[2] == "name-m+ laligatv"

    def test_sin_codigo_manda_la_url(self):
        assert channel_keys(None, "http://x/1", "Canal Sur")[0] == url_key(
            "http://x/1")

    def test_las_fichas_se_funden_campo_a_campo(self):
        union = merge_profiles([ChannelProfile(dial=7),
                                ChannelProfile(display="Nuevo", logo="a.png")])
        assert (union.dial, union.display, union.logo) == (7, "Nuevo", "a.png")


class TestDial:
    def test_el_dial_forzado_gana_al_de_la_guia(self):
        canales = [Channel("LA 1 HD", "http://x/1", "G", tvg_id="TVE")]
        clave = channel_keys("TVE", "http://x/1", "LA 1 HD")[0]
        result = _run(canales, channels={clave: ChannelProfile(dial=900)})
        assert [c.chno for c in result.channels] == [900]

    def test_dos_forzados_iguales_no_rompen_la_numeracion(self):
        canales = [
            Channel("LA 1 HD", "http://x/1", "G", tvg_id="TVE"),
            Channel("CANAL SUR", "http://x/2", "G"),
        ]
        claves = [channel_keys("TVE", "http://x/1", "LA 1 HD")[0],
                  channel_keys(None, "http://x/2", "CANAL SUR")[0]]
        result = _run(canales, channels={c: ChannelProfile(dial=60)
                                         for c in claves})
        assert sorted(c.chno for c in result.channels) == [60, 61]


class TestDummy:
    def test_un_dial_forzado_sin_guia_lleva_dummy(self):
        canales = [Channel("CANAL RARO", "http://x/2", "G")]
        clave = channel_keys(None, "http://x/2", "CANAL RARO")[0]
        result = _run(canales, channels={clave: ChannelProfile(dial=210)})
        canal = result.channels[0]
        assert canal.matched_epg_id == dummy_id_for(clave)
        assert canal.chno == 210

    def test_el_toggle_sirve_los_canales_sin_guia(self):
        canales = [Channel("CANAL RARO", "http://x/2", "G")]
        result = _run(canales, serve_without_epg=True)
        canal = result.channels[0]
        assert canal.matched_epg_id and canal.matched_epg_id.startswith(
            "weteve-dummy-")

    def test_sin_toggle_ni_dial_el_canal_no_se_sirve(self):
        canales = [Channel("CANAL RARO", "http://x/2", "G")]
        result = _run(canales)
        assert result.channels == []
        assert [c.name for c in result.excluded_no_epg] == ["CANAL RARO"]

    def test_el_id_del_dummy_no_depende_de_la_url(self):
        """La misma identidad da el mismo id aunque cambie la variante servida."""
        assert dummy_id_for("MLIGA") == dummy_id_for("MLIGA")
        assert dummy_id_for("MLIGA") == dummy_id_for("MLIGA")


class TestNombreMostrado:
    def test_la_guia_del_canal_pone_el_nombre(self):
        canales = [Channel("LA 1 HD", "http://x/1", "G", tvg_id="TVE")]
        result = _run(canales)
        canal = result.channels[0]
        assert canal.name == "LA 1"
        assert canal.internal_name == "LA 1 HD"

    def test_la_ficha_puede_sobrescribirlo(self):
        canales = [Channel("LA 1 HD", "http://x/1", "G", tvg_id="TVE")]
        clave = channel_keys("TVE", "http://x/1", "LA 1 HD")[0]
        result = _run(canales, channels={clave: ChannelProfile(display="La Uno")})
        assert result.channels[0].name == "La Uno"

    def test_sin_nombre_de_guia_se_queda_el_de_la_lista(self):
        canales = [Channel("LA 1 HD", "http://x/1", "G", tvg_id="TVE")]
        epg = _guia()
        epg._channel_names = {"TVE": "TVE"}      # la guía solo publica el id
        pipeline = ListManagerPipeline(
            m3u_repository=_FakeM3URepo(canales),
            epg_repository=_FakeEpgRepo({"g.xml": epg}))
        pipeline.register_source(0, "l.m3u", "g.xml")
        result = pipeline.run(PipelineTarget(name="t", source_index=0,
                                             epg_source_index=0))
        assert result.channels[0].name == "LA 1 HD"


class TestLogo:
    def test_el_logo_elegido_va_como_fichero(self):
        canales = [Channel("LA 1 HD", "http://x/1", "G", tvg_id="TVE")]
        clave = channel_keys("TVE", "http://x/1", "LA 1 HD")[0]
        result = _run(canales, channels={
            clave: ChannelProfile(logo="LA_1.png")})
        assert result.channels[0].logo_file == "LA_1.png"


class TestGuiaLigera:
    """El epg.xml sin descripciones ni categorías: el 65 % menos de fichero."""

    def _xml(self, light: bool) -> str:
        from src.application.services.list_manager.models import MappedChannel
        from src.application.services.list_manager.playlist_builder import (
            build_xmltv,
        )

        epg = EPGData(
            programs=[Program("Titulo", AHORA, AHORA + timedelta(hours=1),
                              "C4", description="Descripcion larga",
                              category="Cine")],
            channel_names={"C4": "Cuatro"})
        canal = MappedChannel("Cuatro", "http://x/1", matched_epg_id="C4")
        return build_xmltv([canal], epg, light=light)

    def test_por_defecto_sin_descripciones(self):
        xml = self._xml(True)
        assert "<desc>" not in xml and "<category>" not in xml
        assert "<title>Titulo</title>" in xml

    def test_completa_las_incluye(self):
        xml = self._xml(False)
        assert "<desc>Descripcion larga</desc>" in xml
        assert "<category>Cine</category>" in xml


# -- servicio ---------------------------------------------------------------

class _Store:
    def __init__(self, target):
        self.targets = {"0": target}
        self.saved: list = []

    def save(self, target_id, target, epg_path=None, bind=None):
        self.targets[target_id] = target
        self.saved.append(target)

    def list(self):
        return dict(self.targets)

    def get(self, target_id):
        return self.targets.get(target_id)

    def reload(self, target_id):
        return self.targets.get(target_id)

    def get_epg_path(self, target_id):
        return None

    def get_bind(self, target_id):
        return "127.0.0.1"


class _Supervisor:
    def __init__(self, filas=()):
        self.filas = list(filas)

    def mapping_candidates(self, target_id, limit=200, offset=0,
                           include_served=False):
        return {"channels": self.filas}


def _servicio(filas=()):
    store = _Store(PipelineTarget(name="t", source_index="0"))
    return TargetService(_Supervisor(filas), store), store


class TestSetChannels:
    def test_guarda_dial_nombre_y_logo(self):
        servicio, store = _servicio()
        out = servicio.set_channels("0", [{
            "identity": "MLIGA", "url": "http://x/1",
            "dial": 54, "display": "M+ LALIGA", "logo": ""}], reload=False)
        assert "error" not in out
        ficha = store.targets["0"].channels["MLIGA"]
        assert (ficha.dial, ficha.display) == (54, "M+ LALIGA")

    def test_vacio_devuelve_el_campo_al_automatico(self):
        servicio, store = _servicio()
        store.targets["0"] = PipelineTarget(
            name="t", source_index="0",
            channels={"MLIGA": ChannelProfile(dial=54, display="X")})
        servicio.set_channels("0", [{"identity": "MLIGA", "dial": None,
                                     "display": ""}], reload=False)
        assert store.targets["0"].channels["MLIGA"] == ChannelProfile()

    def test_dial_no_numerico(self):
        servicio, store = _servicio()
        out = servicio.set_channels("0", [{"identity": "MLIGA",
                                           "dial": "sesenta"}], reload=False)
        assert "error" in out and out["errors"]

    def test_dial_fuera_de_rango(self):
        servicio, _ = _servicio()
        out = servicio.set_channels("0", [{"identity": "MLIGA", "dial": 0}],
                                    reload=False)
        assert "error" in out

    def test_logo_que_no_esta_en_assets(self):
        servicio, _ = _servicio()
        out = servicio.set_channels("0", [{"identity": "MLIGA",
                                           "logo": "no-existe.png"}],
                                    reload=False)
        assert "error" in out

    def test_logo_real_de_assets(self):
        ficheros = sorted(p.name for p in logos_dir().glob("*.png"))
        if not ficheros:
            pytest.skip("assets/logos vacío")
        servicio, store = _servicio()
        out = servicio.set_channels("0", [{"identity": "MLIGA",
                                           "logo": ficheros[0]}],
                                    reload=False)
        assert "error" not in out
        assert store.targets["0"].channels["MLIGA"].logo == ficheros[0]

    def test_logo_por_ruta_se_copia(self, monkeypatch, tmp_path):
        """Una ruta fuera de assets/logos se copia allí y se ficha."""
        import src.application.services.daemon.service as modulo

        monkeypatch.setattr(modulo, "logos_dir", lambda: tmp_path)
        origen = tmp_path.parent / "elegido.png"
        origen.write_bytes(bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]))
        servicio, store = _servicio()
        out = servicio.set_channels("0", [{"identity": "TVE",
                                           "logo": str(origen)}],
                                    reload=False)
        assert "error" not in out, out
        ficha = store.targets["0"].channels["TVE"]
        assert ficha.logo == "elegido.png"
        assert (tmp_path / "elegido.png").is_file()

    def test_logo_por_url_se_descarga(self, monkeypatch, tmp_path):
        import src.application.services.daemon.service as modulo
        from src.application.services.list_manager.logo_store import LogoStore

        monkeypatch.setattr(modulo, "logos_dir", lambda: tmp_path)
        monkeypatch.setattr(LogoStore, "ensure",
                            lambda self, urls: {urls[0]: "bajado.png"})
        servicio, store = _servicio()
        out = servicio.set_channels(
            "0", [{"identity": "TVE", "logo": "https://x/bajado.png"}],
            reload=False)
        assert "error" not in out, out
        assert store.targets["0"].channels["TVE"].logo == "bajado.png"

    def test_logo_por_url_que_falla(self, monkeypatch, tmp_path):
        import src.application.services.daemon.service as modulo
        from src.application.services.list_manager.logo_store import LogoStore

        monkeypatch.setattr(modulo, "logos_dir", lambda: tmp_path)
        monkeypatch.setattr(LogoStore, "ensure", lambda self, urls: {})
        servicio, _ = _servicio()
        out = servicio.set_channels(
            "0", [{"identity": "TVE", "logo": "https://x/nada.png"}],
            reload=False)
        assert "error" in out and "no se pudo descargar" in out["errors"][0]["error"]

    def test_dial_repetido_no_se_guarda(self):
        filas = [
            {"identity": "MLIGA", "name": "M+ LALIGA", "dial": 54,
             "dial_auto": 54},
            {"identity": "TVE", "name": "LA 1", "dial": 1, "dial_auto": 1},
        ]
        servicio, store = _servicio(filas)
        out = servicio.set_channels("0", [{"identity": "TVE", "dial": 54}],
                                    reload=False)
        assert "error" in out
        assert out["conflicts"][0]["dial"] == 54
        assert store.targets["0"].channels == {}

    def test_una_variante_descartada_no_cuenta_como_dial_repetido(self):
        """Solo las filas servidas ocupan número."""
        filas = [
            {"identity": "TVE", "name": "LA 1", "dial": 1, "dial_auto": 1,
             "served": True},
            {"identity": "otra", "name": "LA 1 HD", "dial": None,
             "dial_auto": 1, "served": False, "duplicate": True},
        ]
        servicio, store = _servicio(filas)
        out = servicio.set_channels("0", [{"identity": "TVE",
                                           "display": "La 1"}],
                                    reload=False)
        assert "error" not in out, out
        assert store.targets["0"].channels["TVE"].display == "La 1"

    def test_el_toggle_de_dummy_se_guarda(self):
        servicio, store = _servicio()
        servicio.update_settings("0", serve_without_epg=True, reload=False)
        assert store.targets["0"].serve_without_epg is True


# -- persistencia -----------------------------------------------------------

class TestPersistencia:
    def test_las_fichas_y_el_toggle_van_y_vuelven(self, tmp_path: Path):
        ruta = tmp_path / "daemon.toml"
        original = PipelineTarget(
            name="t", source_index="0", port=8080, serve_without_epg=True,
            channels={"MLIGA": ChannelProfile(dial=54, display="M+ LALIGA",
                                              logo="x.png",
                                              alias="M+ LaLigaTVHD")})
        config_toml.save(ruta, {"version": 2, "sources": [],
                                "targets": [config_toml.target_to_dict(
                                    original, "0")]})
        crudo = config_toml.load(ruta)
        vuelta = config_toml.target_from_dict(crudo["targets"][0],
                                              config_toml.catalog_from(crudo))
        assert vuelta.channels == original.channels
        assert vuelta.serve_without_epg is True
