"""Servidor MCP Streamable HTTP (JSON-RPC 2.0) sin dependencias externas.

Implementa el núcleo del protocolo MCP (spec 2025-03-26): initialize,
tools/list, tools/call, ping y gestión de sesiones (Mc-Session-Id). El
transporte HTTP vive en ``daemon.py`` (ThreadingHTTPServer); esta clase es el
cerebro puro, testeable sin sockets. Las herramientas se registran en
``mcp_tools.py`` y delegan en el TargetSupervisor.
"""

import json
import logging
import secrets
import threading

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-03-26"

# Códigos JSON-RPC
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
INVALID_SESSION = -32001

class McpStreamableServer:
    """Núcleo JSON-RPC del endpoint MCP. Thread-safe.

    ``handle_request(message, session_id)`` devuelve ``(respuesta, session_id)``
    donde respuesta es None para notificaciones. El transporte mapea eso a
    200 JSON / 202 sin cuerpo.
    """

    def __init__(self, server_name: str = "weteve", server_version: str = "0.1.0",
                 tool_registry=None) -> None:
        self._name = server_name
        self._version = server_version
        self._tools = tool_registry  # dict name -> (schema, handler); set luego
        self._lock = threading.RLock()
        self._sessions: set[str] = set()
        self._initialized: set[str] = set()

    # -- API de transporte -----------------------------------------------------

    def handle_request(self, message: dict, session_id: str | None
                       ) -> tuple[dict | None, str | None]:
        """Procesa un mensaje JSON-RPC. Devuelve (response, session_id).

        - initialize: crea sesión nueva y la devuelve.
        - mensajes siguientes exigen sesión válida.
        - notificaciones (sin id) devuelven respuesta None.
        """
        method = message.get("method", "")
        has_id = "id" in message

        if method == "initialize":
            new_session = secrets.token_hex(16)
            with self._lock:
                self._sessions.add(new_session)
            return self._result(message, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self._name, "version": self._version},
            }), new_session

        with self._lock:
            valid = session_id in self._sessions
        if not valid:
            return self._error(message, INVALID_SESSION,
                               "sesión inválida o expirada"), None

        if method == "notifications/initialized":
            with self._lock:
                self._initialized.add(session_id or "")
            return None, session_id

        if not has_id:
            return None, session_id  # otra notificación: ignorada

        if method == "ping":
            return self._result(message, {}), session_id

        if method == "tools/list":
            return self._result(message, {"tools": self._tools_schema()}), session_id

        if method == "tools/call":
            return self._call_tool(message), session_id

        return self._error(message, METHOD_NOT_FOUND, f"método {method!r}"), session_id

    def handle_delete(self, session_id: str | None) -> None:
        """DELETE /mcp: cierra sesión (idempotente)."""
        with self._lock:
            self._sessions.discard(session_id or "")
            self._initialized.discard(session_id or "")

    # -- herramientas ------------------------------------------------------------

    def set_tool_registry(self, registry: dict) -> None:
        """registry: {name: {"description": str, "schema": dict, "handler": callable}}"""
        self._tools = registry

    def _tools_schema(self) -> list[dict]:
        out = []
        for name, spec in (self._tools or {}).items():
            out.append({
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["schema"],
            })
        return out

    def _call_tool(self, message: dict) -> dict:
        params = message.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        spec = (self._tools or {}).get(name)
        if spec is None:
            return self._error(message, INVALID_PARAMS, f"herramienta {name!r} no existe")
        try:
            result = spec["handler"](**args)
        except TypeError as e:
            return self._error(message, INVALID_PARAMS, f"argumentos inválidos: {e}")
        except Exception as e:
            logger.exception("tool %s falló", name)
            # error de la herramienta: se reporta como contenido, no como error JSON-RPC
            return self._result(message, {
                "content": [{"type": "text", "text": f"error: {e}"}],
                "isError": True,
            })
        return self._result(message, {
            "content": [{"type": "text", "text": _to_text(result)}],
        })

    # -- helpers JSON-RPC ---------------------------------------------------------

    @staticmethod
    def _result(message: dict, result) -> dict:
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": result}

    @staticmethod
    def _error(message: dict, code: int, text: str) -> dict:
        return {"jsonrpc": "2.0", "id": message.get("id"),
                "error": {"code": code, "message": text}}

def _to_text(result) -> str:
    """Serializa el resultado de una herramienta como texto MCP."""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, indent=2)
