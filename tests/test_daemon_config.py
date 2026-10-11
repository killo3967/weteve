"""Tests de la configuración [DAEMON]: control_port, mcp_port, mcp_token."""

import configparser
from pathlib import Path

from src.application.services.daemon.config import (
    DaemonConfig,
    load_daemon_config,
    save_daemon_config,
)


class TestDaemonConfig:
    """Sección [DAEMON] de config.ini, compatible con targets existentes."""

    def test_defaults_when_no_file(self, tmp_path: Path) -> None:
        cfg = load_daemon_config(tmp_path / "nope.ini")
        assert cfg.control_host == "127.0.0.1"
        assert cfg.control_port == 34400
        assert cfg.mcp_port == 34401
        assert cfg.mcp_token == ""

    def test_all_keys_configurable(self, tmp_path: Path) -> None:
        path = tmp_path / "config.ini"
        parser = configparser.ConfigParser()
        parser["DAEMON"] = {
            "control_host": "127.0.0.1",
            "control_port": "39990",
            "mcp_port": "39991",
            "mcp_token": "secreto123",
        }
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)

        cfg = load_daemon_config(path)
        assert cfg.control_port == 39990
        assert cfg.mcp_port == 39991
        assert cfg.mcp_token == "secreto123"

    def test_save_preserves_targets(self, tmp_path: Path) -> None:
        """Guardar [DAEMON] no debe tocar las secciones listmanager.*."""
        path = tmp_path / "config.ini"
        parser = configparser.ConfigParser()
        parser["listmanager.target.0"] = {"name": "Mi lista", "port": "8081"}
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)

        save_daemon_config(path, DaemonConfig(mcp_token="tk"))
        parser2 = configparser.ConfigParser()
        parser2.read(path, encoding="utf-8")
        assert parser2["listmanager.target.0"]["name"] == "Mi lista"
        assert parser2["DAEMON"]["mcp_token"] == "tk"

    def test_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "config.ini"
        original = DaemonConfig(
            control_host="127.0.0.1", control_port=35000,
            mcp_port=35001, mcp_token="abc",
        )
        save_daemon_config(path, original)
        assert load_daemon_config(path) == original
