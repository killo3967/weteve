"""Escritura segura del fichero INI del cliente y del código común.

El antiguo ``config_store`` serializaba los targets y la cartera; eso vive
ahora en ``config_toml`` (ver ``test_config_toml``). Aquí queda lo que sigue
siendo suyo: la escritura atómica con ``.bak``, el INI del cliente
(``weteve.ini``) y el lector del formato viejo que usa ``migrate-config``.
"""

import configparser
from pathlib import Path

import pytest

from src.application.services.daemon.config import DaemonConfig
from src.infrastructure.client import config_store


class TestEscrituraSegura:
    """Un fichero de configuración no puede quedar a medias."""

    def _fichero(self, tmp_path: Path) -> Path:
        path = tmp_path / "weteve.ini"
        path.write_text("[DAEMON]\ncontrol_port = 34400\n", encoding="utf-8")
        return path

    def test_guarda_la_version_anterior_en_bak(self, tmp_path):
        path = self._fichero(tmp_path)
        anterior = path.read_text(encoding="utf-8")
        config_store.write_atomic(path, "[DAEMON]\ncontrol_port = 35000\n")
        assert "35000" in path.read_text(encoding="utf-8")
        assert config_store.backup_path(path).read_text(
            encoding="utf-8") == anterior

    def test_no_deja_temporales(self, tmp_path):
        path = self._fichero(tmp_path)
        config_store.write_atomic(path, "[DAEMON]\ncontrol_port = 1\n")
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "weteve.ini", "weteve.ini.bak"]

    def test_un_fallo_a_mitad_deja_el_fichero_intacto(self, tmp_path,
                                                     monkeypatch):
        path = self._fichero(tmp_path)
        anterior = path.read_text(encoding="utf-8")

        def fsync_roto(_fd):
            raise OSError("disco lleno")

        monkeypatch.setattr(config_store.os, "fsync", fsync_roto)
        with pytest.raises(OSError):
            config_store.write_atomic(path, "[DAEMON]\ncontrol_port = 2\n")
        assert path.read_text(encoding="utf-8") == anterior
        assert not (tmp_path / "weteve.ini.tmp").exists()

    def test_primera_escritura_sin_bak(self, tmp_path):
        path = tmp_path / "nuevo.ini"
        config_store.write_atomic(path, "[DAEMON]\n")
        assert path.exists()
        assert not config_store.backup_path(path).exists()

    def test_crea_la_carpeta_destino(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "weteve.ini"
        config_store.write_atomic(path, "[DAEMON]\n")
        assert path.exists()


class TestFicheroDelCliente:
    """``weteve.ini``: solo ``[DAEMON]``, y el resto no se toca."""

    def _escribe(self, path: Path, cfg: DaemonConfig) -> None:
        config_store.update(path, lambda p: config_store.write_daemon(p, cfg))

    def test_ida_y_vuelta(self, tmp_path: Path):
        from src.application.services.daemon.config import load_daemon_config

        path = tmp_path / "weteve.ini"
        cfg = DaemonConfig(control_host="127.0.0.1", control_port=34990,
                           mcp_port=34990, mcp_token="t0k3n",
                           autostart=True, stop_on_exit=True)
        self._escribe(path, cfg)
        assert load_daemon_config(path) == cfg

    def test_escribir_el_daemon_no_pierde_otras_claves(self, tmp_path: Path):
        """Si el usuario añade claves propias (tema, tamaño…), se conservan."""
        path = tmp_path / "weteve.ini"
        path.write_text(
            "[DAEMON]\ncontrol_port = 34400\n\n[GUI]\ntheme = dark\n",
            encoding="utf-8")
        self._escribe(path, DaemonConfig(control_port=35000))
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(path, encoding="utf-8")
        assert parser.getint("DAEMON", "control_port") == 35000
        assert parser.get("GUI", "theme") == "dark"


class TestLecturaDelFormatoViejo:
    """``parse_overrides`` solo lo usa ``migrate-config``: lee los dos formatos."""

    def test_un_mapeo_por_linea(self):
        crudo = "\nhttp://a/1::es.uno\nhttp://a/2::es.dos"
        assert config_store.parse_overrides(crudo) == {
            "http://a/1": "es.uno", "http://a/2": "es.dos"}

    def test_el_formato_viejo_de_una_linea(self):
        crudo = "http://a/1::es.uno;;http://a/2::es.dos"
        assert config_store.parse_overrides(crudo) == {
            "http://a/1": "es.uno", "http://a/2": "es.dos"}

    def test_url_con_porcentaje_y_sin_mapeo(self):
        assert config_store.parse_overrides(
            "http://a/mi%20lista.m3u::es.uno\nbasura") == {
            "http://a/mi%20lista.m3u": "es.uno"}
