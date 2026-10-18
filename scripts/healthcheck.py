#!/usr/bin/env python3
"""Healthcheck manual de weteve: puertos, fuentes, formato y MCP.

Uso:
    python scripts/healthcheck.py            # usa config.ini del repo
    python scripts/healthcheck.py --config C:/ruta/config.ini

Comprobaciones (cada una OK / FAIL / SKIP, nunca lanza excepción):
  1. Daemon: API de control responde /health y MCP responde initialize.
  2. MCP: tools/list contiene todas las herramientas de EXPECTED_TOOLS
     (las extra se informan, no fallan).
  3. Targets: get_status de cada target (sirviendo / error / puerto).
  4. Puertos: el puerto de cada target sirviendo acepta conexiones y sirve
     /playlist.m3u con cabecera #EXTM3U (y /epg.xml con <tv> si hay EPG).
  5. Fuentes: cada entrada de la cartera (file: existe y formato válido;
     url: descarga con timeout y formato válido).

Exit code: 0 si no hay FAILs, 1 si hay alguno (SKIP no cuenta).
"""

from __future__ import annotations

import argparse
import configparser
import gzip
import http.client
import json
import socket
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
EXPECTED_TOOLS = {
    "get_status", "shutdown", "list_targets", "get_target",
    "create_target", "update_target", "delete_target",
    "start_target", "stop_target", "reload_target",
    "run_pipeline", "list_tv_channels", "list_epg_channels",
    "suggest_epg_matches",
    "add_source", "list_sources", "update_source", "remove_source",
    "set_target_sources",
    "create_filter", "delete_filter", "modify_mapping", "modify_mappings",
}

_results: list[tuple[str, str, str]] = []  # (estado, sección, detalle)


def report(state: str, section: str, detail: str) -> None:
    _results.append((state, section, detail))
    icon = {"OK": "[OK]  ", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[state]
    print(f"{icon} {section}: {detail}")


def section(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, 60 - len(title)))


# ----------------------------------------------------------------- fuentes

def check_m3u_bytes(data: bytes, label: str) -> str | None:
    """Devuelve None si es válido, o el motivo del fallo."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return "fichero vacío"
    if not lines[0].startswith("#EXTM3U"):
        return f"primera línea no es #EXTM3U ({lines[0][:50]!r})"
    if not any(l.startswith("#EXTINF") for l in lines):
        return "sin #EXTINF"
    return None


def check_epg_bytes(data: bytes, label: str) -> str | None:
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError as e:
            return f"gzip corrupto: {e}"
    try:
        root = ET.fromstring(data.decode("utf-8", errors="replace"))
    except ET.ParseError as e:
        return f"XML mal formado: {e}"
    if root.tag != "tv":
        return f"raíz <{root.tag}>, esperada <tv>"
    return None


def check_sources(cfg_path: Path) -> None:
    section("Fuentes (cartera config.ini)")
    from src.infrastructure.client.sources_store import load_catalog
    catalog = load_catalog(cfg_path)
    entries = catalog.all()
    if not entries:
        report("SKIP", "fuentes", "cartera vacía")
        return
    for e in entries:
        label = f"{e.name} [{e.kind}]"
        if e.source_type == "file":
            p = Path(e.location)
            if not p.exists():
                report("FAIL", label, f"no existe: {e.location}")
                continue
            try:
                data = p.read_bytes()
            except OSError as err:
                report("FAIL", label, f"no legible: {err}")
                continue
            reason = (check_m3u_bytes(data, label) if e.kind == "m3u"
                      else check_epg_bytes(data, label))
            if reason:
                report("FAIL", label, f"{e.location} — {reason}")
            else:
                report("OK", label, f"{e.location} ({len(data) // 1024} KB)")
        else:
            try:
                req = urllib.request.Request(e.location,
                                             headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = r.read(20 * 1024 * 1024)
                reason = (check_m3u_bytes(data, label) if e.kind == "m3u"
                          else check_epg_bytes(data, label))
                if reason:
                    report("FAIL", label, f"{e.location} — {reason}")
                else:
                    report("OK", label,
                           f"{e.location} ({len(data) // 1024} KB descargados)")
            except (urllib.error.URLError, OSError) as err:
                report("FAIL", label, f"{e.location} — descarga: {err}")


# ------------------------------------------------------------------- daemon

def http_get(url: str, timeout: int = 5, attempts: int = 3) -> tuple[int, bytes]:
    """GET con reintentos cortos (flake RST de loopback en Windows)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
        except OSError as e:
            last = e
            time.sleep(0.25 * (i + 1))
    raise OSError(str(last))


def _mcp_call(host: str, port: int, method: str, params: dict | None,
              session: str | None = None, token: str = ""
              ) -> tuple[int, str | None, dict]:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if session:
        headers["Mcp-Session-Id"] = session
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("POST", "/mcp", body=json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
        headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    sid = resp.getheader("Mcp-Session-Id")
    conn.close()
    try:
        return resp.status, sid, json.loads(body)
    except json.JSONDecodeError:
        return resp.status, sid, {}


def check_daemon(cfg_path: Path) -> dict:
    """Devuelve {'host', 'port', 'session', 'targets'} del daemon vivo."""
    section("Daemon (un solo puerto)")
    parser = configparser.ConfigParser()
    parser.read(cfg_path, encoding="utf-8")
    host = parser.get("DAEMON", "control_host", fallback="127.0.0.1")
    # Todo (control, REST, MCP y playlists) se sirve en control_port.
    cport = parser.getint("DAEMON", "control_port", fallback=34400)
    mport = cport
    token = parser.get("DAEMON", "mcp_token", fallback="")
    legacy = parser.getint("DAEMON", "mcp_port", fallback=cport)
    if legacy != cport:
        report("WARN" if False else "OK", "puerto MCP",
               f"mcp_port={legacy} se ignora: todo va por {cport}")

    status: dict = {}
    try:
        code, body = http_get(f"http://{host}:{cport}/health", timeout=5)
        if code == 200:
            report("OK", "API control",
                   f"http://{host}:{cport}/health responde")
        else:
            report("FAIL", "API control", f"HTTP {code}")
    except OSError as e:
        report("FAIL", "API control",
               f"no responde en {host}:{cport} — ¿daemon parado? ({e})")

    session = None
    try:
        code, sid, resp = _mcp_call(host, mport, "initialize",
                                    {"protocolVersion": "2025-03-26",
                                     "capabilities": {},
                                     "clientInfo": {"name": "healthcheck",
                                                    "version": "0"}},
                                    token=token)
        if code == 200 and sid:
            session = sid
            report("OK", "MCP", f"http://{host}:{mport}/mcp acepta initialize")
        else:
            report("FAIL", "MCP", f"initialize → HTTP {code}: {resp}")
    except OSError as e:
        report("FAIL", "MCP",
               f"no responde en {host}:{mport} — ¿daemon parado? ({e})")

    tools_ok = False
    if session:
        try:
            code, _, resp = _mcp_call(host, mport, "tools/list", {},
                                      session=session, token=token)
            names = {t["name"] for t in resp.get("result", {}).get("tools", [])}
            missing = EXPECTED_TOOLS - names
            extra = names - EXPECTED_TOOLS
            if code == 200 and not missing:
                report("OK", "MCP tools",
                       f"{len(names)} herramientas (esperadas {len(EXPECTED_TOOLS)})"
                       + (f"; extra: {sorted(extra)}" if extra else ""))
                tools_ok = True
            else:
                report("FAIL", "MCP tools",
                       f"faltan: {sorted(missing) or '—'}; recibidas: {len(names)}")
        except OSError as e:
            report("FAIL", "MCP tools", f"tools/list: {e}")

    # estado de targets vía tools/call get_status
    if tools_ok:
        try:
            code, _, resp = _mcp_call(
                host, mport, "tools/call",
                {"name": "get_status", "arguments": {}},
                session=session, token=token)
            text = resp.get("result", {}).get("content", [{}])[0].get("text", "{}")
            status = {"targets": json.loads(text).get("targets", {})}
            targets = status["targets"]
            if not targets:
                report("SKIP", "targets", "no hay targets definidos")
            for tid, t in targets.items():
                if t.get("running"):
                    report("OK", f"target {t.get('name', tid)}",
                           f"sirviendo en :{t.get('port')} "
                           f"({t.get('channel_count', '?')} canales)")
                else:
                    report("FAIL", f"target {t.get('name', tid)}",
                           f"parado o con error: {t.get('error', 'sin error')}")
        except (OSError, KeyError, json.JSONDecodeError) as e:
            report("FAIL", "targets", f"get_status: {e}")

    return {"host": host, "port": cport, "session": session,
            "token": token, "targets": status.get("targets", {}),
            "tools_ok": tools_ok}


def check_serving(info: dict) -> None:
    """Playlist y EPG de cada target por HTTP real (todo en el puerto único)."""
    section("Servicio (playlist/EPG servidos)")
    serving = {tid: t for tid, t in info.get("targets", {}).items()
               if t.get("running")}
    if not serving:
        report("SKIP", "servicio", "ningún target sirviendo")
        return
    host = info.get("host", "127.0.0.1")
    port = info.get("port") or 0
    for tid, t in serving.items():
        name = t.get("name", tid)
        base = f"http://{host}:{port}/weteve/{tid}"
        # puerto abierto
        s = socket.socket()
        s.settimeout(3)
        try:
            s.connect((host, port))
            open_ok = True
        except OSError as e:
            open_ok = False
            report("FAIL", f"puerto {name}", f":{port} no acepta conexiones ({e})")
        finally:
            s.close()
        if not open_ok:
            continue
        report("OK", f"puerto {name}", f":{port} abierto")
        # playlist
        try:
            code, body = http_get(f"{base}/playlist.m3u")
            reason = check_m3u_bytes(body, name) if code == 200 else f"HTTP {code}"
            if reason:
                report("FAIL", f"playlist {name}", reason)
            else:
                n = sum(1 for l in body.decode("utf-8", "replace").splitlines()
                        if l.startswith("#EXTINF"))
                report("OK", f"playlist {name}",
                       f"{base}/playlist.m3u ({n} canales)")
        except OSError as e:
            report("FAIL", f"playlist {name}", str(e))
        # epg (el daemon expone /epg.xml aunque esté vacío)
        try:
            code, body = http_get(f"{base}/epg.xml")
            if code != 200:
                report("SKIP", f"epg {name}", f"HTTP {code} (target sin EPG)")
            elif not body.strip():
                report("SKIP", f"epg {name}", "vacío (target sin EPG asignado)")
            else:
                reason = check_epg_bytes(body, name)
                if reason:
                    report("FAIL", f"epg {name}", reason)
                else:
                    report("OK", f"epg {name}", f"{base}/epg.xml válido")
        except OSError as e:
            report("SKIP", f"epg {name}", f"no accesible: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Healthcheck de weteve")
    ap.add_argument("--config", default=str(REPO / "config.ini"))
    args = ap.parse_args()
    cfg_path = Path(args.config)

    print(f"weteve healthcheck — {cfg_path}")
    sys.path.insert(0, str(REPO))

    check_sources(cfg_path)
    info = check_daemon(cfg_path)
    check_serving(info)

    fails = sum(1 for s, _, _ in _results if s == "FAIL")
    oks = sum(1 for s, _, _ in _results if s == "OK")
    skips = sum(1 for s, _, _ in _results if s == "SKIP")
    print("\n=== Resumen: "
          f"{oks} OK, {fails} FAIL, {skips} SKIP ===")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
