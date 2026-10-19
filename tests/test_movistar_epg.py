"""Adaptador de la guía de Movistar+ (API OTT) y router de fuentes EPG."""


import pytest

from src.domain.entities.epg import ChannelInfo, EPGData
from src.infrastructure.adapters.epg_repository_router import EPGRepositoryRouter
from src.infrastructure.adapters.movistar_epg_repository import (
    MovistarEPGRepository,
)
from src.infrastructure.adapters.xmltv_repository import XMLTVRepository

CANALES = [
    {"CodCadenaTv": "TVE", "Nombre": "LA 1", "Dial": 1,
     "Logo": "https://logos/TVE.png",
     "Logos": [{"id": "default", "uri": "https://logos/TVE-alt.png"}]},
    {"CodCadenaTv": "A3", "Nombre": "Antena 3", "Dial": "3",
     "Logo": "", "Logos": [{"id": "default", "uri": "https://logos/A3.png"}]},
    {"CodCadenaTv": "SIN", "Nombre": "Sin dial", "Dial": 0, "Logo": ""},
]

MS = 1_760_000_000_000  # marca de tiempo cualquiera en ms


@pytest.fixture()
def repo() -> MovistarEPGRepository:
    return MovistarEPGRepository(ttl_seconds=0)


class TestMovistarEPG:
    def _instalar(self, monkeypatch, repo, canales=None, programas=None,
                  registro=None) -> None:
        canales = CANALES if canales is None else canales
        programas = programas if programas is not None else [
            {"Titulo": "Noticias", "FechaHoraInicio": MS,
             "FechaHoraFin": MS + 3_600_000, "GeneroComAntena": "Informativos"},
        ]

        def fake_get_json(url: str, *args, **kwargs):
            if registro is not None:
                registro.append(url)
            if "contents/channels" in url:
                return canales
            return programas

        monkeypatch.setattr(repo, "_get_json", fake_get_json)

    def test_canales_aportan_dial_logo_y_nombre(self, monkeypatch, repo):
        self._instalar(monkeypatch, repo)
        epg = repo.load_epg("movistar://epg")
        assert epg.channel_names["TVE"] == "LA 1"
        assert epg.channel_info["TVE"] == ChannelInfo(dial=1,
                                                     logo="https://logos/TVE.png")
        # Dial en texto y logo solo en el catálogo: se normalizan igual.
        assert epg.channel_info["A3"] == ChannelInfo(dial=3,
                                                    logo="https://logos/A3.png")
        # Dial 0 = sin dial (no es un canal del dial).
        assert epg.channel_info["SIN"].dial is None

    def test_programas_se_convierten_a_datetime(self, monkeypatch, repo):
        self._instalar(monkeypatch, repo)
        epg = repo.load_epg("movistar://epg")
        programas = epg.get_programs_for_channel("TVE")
        assert len(programas) == 1
        assert programas[0].title == "Noticias"
        assert programas[0].category == "Informativos"
        assert programas[0].start_time < programas[0].end_time

    def test_segunda_llamada_usa_la_cache(self, monkeypatch):
        repo = MovistarEPGRepository(ttl_seconds=300)
        registro: list[str] = []
        self._instalar(monkeypatch, repo, registro=registro)
        repo.load_epg("movistar://epg")
        primera = len(registro)
        repo.load_epg("movistar://epg")
        assert primera > 0
        assert len(registro) == primera

    def test_fresh_vuelve_a_descargar(self, monkeypatch):
        repo = MovistarEPGRepository(ttl_seconds=300)
        registro: list[str] = []
        self._instalar(monkeypatch, repo, registro=registro)
        repo.load_epg("movistar://epg")
        primera = len(registro)
        repo.load_epg("movistar://epg", fresh=True)
        assert len(registro) > primera

    def test_span_de_la_fuente_llega_a_la_peticion(self, monkeypatch):
        registro: list[str] = []
        repo = MovistarEPGRepository(ttl_seconds=0)
        self._instalar(monkeypatch, repo, registro=registro)
        repo.load_epg("movistar://epg?span=7")
        assert any("span=7" in url for url in registro)

    def test_span_se_limita_al_maximo(self, monkeypatch):
        registro: list[str] = []
        repo = MovistarEPGRepository(ttl_seconds=0)
        self._instalar(monkeypatch, repo, registro=registro)
        repo.load_epg("movistar://epg?span=99")
        assert any("span=7" in url for url in registro)

    def test_un_fallo_devuelve_guia_vacia(self, monkeypatch, repo):
        def boom(url: str, *args, **kwargs):
            raise RuntimeError("sin red")

        monkeypatch.setattr(repo, "_get_json", boom)
        epg = repo.load_epg("movistar://epg")
        assert isinstance(epg, EPGData)
        assert epg.programs == []
        assert epg.channel_info == {}

    def test_canal_sin_parrilla_no_tumba_la_carga(self, monkeypatch, repo):
        def fake_get_json(url: str, *args, **kwargs):
            if "contents/channels" in url:
                return CANALES
            if "channel=A3" in url:
                raise RuntimeError("caído")
            return [{"Titulo": "P", "FechaHoraInicio": MS,
                     "FechaHoraFin": MS + 60_000}]

        monkeypatch.setattr(repo, "_get_json", fake_get_json)
        epg = repo.load_epg("movistar://epg")
        assert epg.channel_names  # sigue habiendo canales y guía de los demás
        assert len(epg.programs) == 2


class TestRouterDeGuias:
    def test_movistar_va_al_adaptador_de_movistar(self):
        router = EPGRepositoryRouter()
        assert isinstance(router.repository_for("movistar://epg"),
                          MovistarEPGRepository)
        assert isinstance(router.repository_for("MOVISTAR://epg?span=3"),
                          MovistarEPGRepository)

    def test_http_y_fichero_van_al_lector_xmltv(self):
        router = EPGRepositoryRouter()
        assert isinstance(router.repository_for("https://x/y.xml.gz"),
                          XMLTVRepository)
        assert isinstance(router.repository_for("C:/datos/guia.xml"),
                          XMLTVRepository)

    def test_delega_en_el_adaptador_que_toca(self):
        class _Doble:
            def __init__(self) -> None:
                self.llamadas: list[tuple[str, bool]] = []

            def load_epg(self, source: str, fresh: bool = False) -> EPGData:
                self.llamadas.append((source, fresh))
                return EPGData(channel_names={"x": "X"})

        movistar, default = _Doble(), _Doble()
        router = EPGRepositoryRouter(routes=[("movistar:", movistar)],
                                     default=default)
        router.load_epg("movistar://epg", fresh=True)
        router.load_epg("C:/guia.xml")
        assert movistar.llamadas == [("movistar://epg", True)]
        assert default.llamadas == [("C:/guia.xml", False)]
