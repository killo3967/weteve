"""Logos locales: se descargan una vez y los sirve el propio demonio."""

from types import SimpleNamespace

from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.list_manager.logo_store import LogoStore
from src.application.services.list_manager.models import MappedChannel
from src.application.services.list_manager.pipeline import PipelineResult
from src.infrastructure.web.playlist_server import url_base_for

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


class _Descargas:
    def __init__(self, datos=PNG):
        self.urls: list[str] = []
        self._datos = datos

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return self._datos


class TestLogoStore:
    def test_descarga_y_guarda_con_el_nombre_original(self, tmp_path):
        store = LogoStore(tmp_path, opener=_Descargas())
        mapos = store.ensure(["https://x/recorte/guiamovil/TVE.png"])
        assert mapos == {"https://x/recorte/guiamovil/TVE.png": "TVE.png"}
        assert (tmp_path / "TVE.png").read_bytes() == PNG

    def test_la_segunda_vez_no_descarga(self, tmp_path):
        descargas = _Descargas()
        store = LogoStore(tmp_path, opener=descargas)
        store.ensure(["https://x/A3.png"])
        store.ensure(["https://x/A3.png"])
        assert descargas.urls == ["https://x/A3.png"]

    def test_un_fichero_valido_en_disco_no_se_pide(self, tmp_path):
        (tmp_path / "C4.png").write_bytes(PNG)
        descargas = _Descargas()
        mapos = LogoStore(tmp_path, opener=descargas).ensure(["https://x/C4.png"])
        assert mapos == {"https://x/C4.png": "C4.png"}
        assert descargas.urls == []

    def test_lo_que_no_es_imagen_se_descarta(self, tmp_path):
        store = LogoStore(tmp_path, opener=_Descargas(b"<html>no</html>"))
        assert store.ensure(["https://x/T5.png"]) == {}
        assert not (tmp_path / "T5.png").exists()

    def test_url_sin_extension_anade_png(self, tmp_path):
        # los CDN de Movistar sirven .../recorte/m-DP/wpmos/TVE sin extensión
        store = LogoStore(tmp_path, opener=_Descargas())
        mapos = store.ensure(["https://x/recorte/m-DP/wpmos/TVE"])
        assert mapos["https://x/recorte/m-DP/wpmos/TVE"] == "TVE.png"

    def test_un_nombre_inservible_cae_al_hash(self, tmp_path):
        store = LogoStore(tmp_path, opener=_Descargas())
        mapos = store.ensure(["https://x/logo.php?h=1"])
        nombre = mapos["https://x/logo.php?h=1"]
        assert nombre.endswith(".png") and len(nombre) == 20

    def test_un_fallo_no_rompe(self, tmp_path):
        def boom(url: str) -> bytes:
            raise OSError("sin red")

        assert LogoStore(tmp_path, opener=boom).ensure(["https://x/A3.png"]) == {}


class TestPlaylistConLogosLocales:
    def _supervisor(self, tmp_path):
        store = LogoStore(tmp_path, opener=_Descargas())
        return TargetSupervisor(logo_store=store), tmp_path

    def _result(self):
        canal = MappedChannel(name="LA 1", url="http://s/1", group="G",
                              logo_url="https://x/TVE.png", chno=1)
        return PipelineResult([canal], 1, [])

    def test_la_playlist_apunta_al_logo_local(self, tmp_path):
        supervisor, dir_logos = self._supervisor(tmp_path)
        target = SimpleNamespace(bind="127.0.0.1", port=34400)
        canales = supervisor._channels_with_local_logos(
            target, self._result().channels)
        assert canales[0].logo_url == "http://127.0.0.1:34400/logos/TVE.png"
        assert (dir_logos / "TVE.png").exists()

    def test_sin_logo_descargable_se_queda_la_url_remota(self, tmp_path):
        store = LogoStore(tmp_path, opener=lambda u: b"no soy una imagen")
        supervisor = TargetSupervisor(logo_store=store)
        target = SimpleNamespace(bind="127.0.0.1", port=34400)
        canales = supervisor._channels_with_local_logos(
            target, self._result().channels)
        assert canales[0].logo_url == "https://x/TVE.png"

    def test_la_ruta_sirve_la_imagen(self, tmp_path):
        supervisor, dir_logos = self._supervisor(tmp_path)
        dir_logos.mkdir(parents=True, exist_ok=True)
        (dir_logos / "TVE.png").write_bytes(PNG)
        status, cuerpo, tipo = supervisor.logo_route("TVE.png")
        assert status == 200 and cuerpo == PNG and tipo == "image/png"

    def test_no_se_puede_salir_del_directorio(self, tmp_path):
        supervisor, _ = self._supervisor(tmp_path)
        for nombre in ("../secreto.png", "..\\secreto.png", ""):
            status, _, _ = supervisor.logo_route(nombre)
            assert status == 404

    def test_playlist_route_reconoce_los_logos(self, tmp_path):
        supervisor, dir_logos = self._supervisor(tmp_path)
        dir_logos.mkdir(parents=True, exist_ok=True)
        (dir_logos / "A3.png").write_bytes(PNG)
        for ruta in ("/logos/A3.png", "/weteve/logos/A3.png",
                     "/weteve/0/logos/A3.png"):
            salida = supervisor.playlist_route(ruta)
            assert salida is not None and salida[0] == 200, ruta
            assert salida[1] == PNG


class TestUrlBase:
    def test_localhost(self):
        assert url_base_for("127.0.0.1", 34400) == "http://127.0.0.1:34400"

    def test_wildcard_no_se_publica_como_0000(self):
        base = url_base_for("0.0.0.0", 34400)
        assert "0.0.0.0" not in base
        assert base.endswith(":34400")
