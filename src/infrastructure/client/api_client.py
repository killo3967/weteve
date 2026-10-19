"""Cliente HTTP de la API REST v1 del daemon.

La GUI de Qt es un frontend: todo lo que sabe del motor lo pregunta por aquí.
Se habla con el mismo puerto que MCP (``[DAEMON] mcp_port``) bajo ``/api/v1``.

Dos decisiones deliberadas:

- ``http.client`` en vez de ``urllib``: urllib resuelve la configuración de
  proxy de Windows en cada petición (~2 s por intento con el daemon caído, que
  es lo que congelaba la GUI). Aquí es loopback puro.
- Reintento corto en errores de conexión: el loopback de Windows devuelve RST
  intermitente; un reintento cuesta milisegundos.

Los errores no lanzan: devuelven ``(0, {"error": ...})`` si el daemon no
responde, para que la UI avise sin capturar excepciones de red.
"""

import http.client
import json
import time
import urllib.parse

# El pipeline reejecuta la M3U y las guías completas (decenas de miles de
# canales, dos XMLTV): la llamada que lo dispara espera a que termine en vez
# de cortar al timeout corto del resto de peticiones.
PIPELINE_TIMEOUT = 180.0


class ApiClient:
    """Cliente mínimo de ``/api/v1`` (GET/PUT/POST)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 34401,
                 token: str = "", timeout: float = 5.0, retries: int = 2,
                 retry_delay: float = 0.05) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._timeout = timeout
        self._retries = retries
        self._retry_delay = retry_delay
        # Un puerto cerrado tarda ~2 s en rechazar en Windows: si el demonio no
        # está, no se vuelve a intentar durante unos segundos. Sin esto, cada
        # consulta de la UI pagaba esos 2 s (la ventana tardaba ~90 s en abrir
        # con 20 reglas).
        self._down_until = 0.0
        self._down_seconds = 5.0

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}/api/v1"

    @property
    def root_url(self) -> str:
        """Raíz del demonio: control, API, MCP y playlists en el mismo puerto."""
        return f"http://{self._host}:{self._port}"

    def request(self, method: str, path: str,
                payload: dict | None = None,
                timeout: float | None = None) -> tuple[int, dict]:
        """(status, body). status 0 = daemon no accesible.

        ``timeout`` permite una espera mayor en las llamadas que ejecutan el
        pipeline (decenas de miles de canales y dos guías tardan segundos).
        """
        if time.monotonic() < self._down_until:
            return 0, {"error": "demonio no disponible (intento reciente falló)"}
        wait = self._timeout if timeout is None else float(timeout)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            conn = http.client.HTTPConnection(self._host, self._port,
                                              timeout=wait)
            try:
                conn.request(method, "/api/v1" + path, body=body,
                             headers=headers)
                resp = conn.getresponse()
                raw = resp.read().decode("utf-8")
                self._down_until = 0.0  # el demonio responde otra vez
                return resp.status, (json.loads(raw) if raw else {})
            except (ConnectionRefusedError, TimeoutError) as e:
                # no escucha o no contesta: reintentar no cambia nada (y cada
                # intento cuesta ~2 s en Windows)
                last = e
                self._down_until = time.monotonic() + self._down_seconds
                break
            except (ConnectionError, OSError) as e:
                last = e
                if attempt < self._retries:
                    time.sleep(self._retry_delay)
            finally:
                conn.close()
        return 0, {"error": str(last)}

    # -- verbos --------------------------------------------------------------

    def get(self, path: str, **params) -> tuple[int, dict]:
        clean = {k: v for k, v in params.items() if v is not None}
        query = urllib.parse.urlencode(clean)
        return self.request("GET", f"{path}?{query}" if query else path)

    def put(self, path: str, payload: dict,
            timeout: float | None = None) -> tuple[int, dict]:
        return self.request("PUT", path, payload, timeout=timeout)

    def post(self, path: str, payload: dict | None = None,
             timeout: float | None = None) -> tuple[int, dict]:
        return self.request("POST", path, payload if payload is not None else {},
                            timeout=timeout)

    # -- atajos de la UI -----------------------------------------------------

    def status(self) -> tuple[int, dict]:
        return self.get("/status")

    def target_status(self, target_id: str) -> tuple[int, dict]:
        return self.get(f"/targets/{target_id}/status")

    def shutdown(self) -> tuple[int, dict]:
        """Pide al demonio que se apague (mismo puerto, misma API)."""
        return self.post("/shutdown")

    def service(self, target_id: str, action: str) -> tuple[int, dict]:
        """Arranca o para el servidor del target en el daemon.

        Arrancar ejecuta el pipeline (segundos): espera larga, o la GUI corta
        a los 5 s y avisa de un fallo que no ha ocurrido.
        """
        return self.post(f"/targets/{target_id}/service",
                         {"action": action}, timeout=PIPELINE_TIMEOUT)

    def sources(self) -> tuple[int, dict]:
        return self.get("/sources")

    def set_sources(self, sources: list[dict]) -> tuple[int, dict]:
        """Reemplaza la cartera completa (filas que la UI ve)."""
        return self.put("/sources", {"sources": sources})

    def set_target_sources(self, target_id: str, m3u_ids: list[str],
                           epg_ids: list[str],
                           reload: bool = True) -> tuple[int, dict]:
        """Fija las fuentes del target.

        ``reload=False`` es obligatorio en el guardado por lotes de la UI: sin
        él cada paso reejecuta el pipeline (varios segundos) y el cliente
        corta por timeout antes de terminar.
        """
        return self.put(f"/targets/{target_id}/sources",
                        {"m3u_sources": m3u_ids, "epg_sources": epg_ids,
                         "reload": reload})

    def targets(self) -> tuple[int, dict]:
        """Lista de targets del demonio (para el selector de Configuración)."""
        return self.get("/targets")

    def mapping_candidates(self, target_id: str,
                           limit: int = 1000, offset: int = 0,
                           include_served: bool = True) -> tuple[int, dict]:
        """Una página de canales para la tabla de mapeo.

        Por defecto incluye también los ya servidos con guía: sin ellos no se
        puede revisar ni cambiar la EPG de un canal que ya funciona.
        """
        return self.get(f"/targets/{target_id}/mapping-candidates",
                        limit=limit, offset=offset,
                        include_served="true" if include_served else "false")

    def all_mapping_candidates(self, target_id: str,
                               include_served: bool = True) -> tuple[int, dict]:
        """Todos los canales de la tabla, paginando hasta ``total``."""
        return self._all_pages(
            lambda offset: self.mapping_candidates(
                target_id, offset=offset, include_served=include_served))

    def epg_channels(self, target_id: str, limit: int = 1000,
                     offset: int = 0) -> tuple[int, dict]:
        return self.get(f"/targets/{target_id}/epg-channels",
                        limit=limit, offset=offset)

    def all_epg_channels(self, target_id: str) -> tuple[int, dict]:
        """Catálogo EPG completo: un desplegable no puede perder canales."""
        return self._all_pages(
            lambda offset: self.epg_channels(target_id, offset=offset))

    def epg_catalog(self, target_id: str, limit: int = 1000,
                    offset: int = 0) -> tuple[int, dict]:
        """Catálogo de la guía: dial, nombre, código interno y estado."""
        return self.get(f"/targets/{target_id}/catalog",
                        limit=limit, offset=offset)

    def all_epg_catalog(self, target_id: str) -> tuple[int, dict]:
        """Tabla de referencia completa (todas las páginas)."""
        return self._all_pages(
            lambda offset: self.epg_catalog(target_id, offset=offset))

    def _all_pages(self, fetch,
                   max_pages: int = 100) -> tuple[int, dict]:
        """Recorre las páginas hasta ``total`` y devuelve una sola respuesta.

        ``truncated`` avisa si el daemon no dejó seguir (respuesta corta o
        tope de páginas): la UI lo dice en vez de perder canales en silencio.
        """
        rows: list = []
        first: dict = {}
        offset = 0
        for _ in range(max_pages):
            code, body = fetch(offset)
            if code != 200:
                return code, body
            if not first:
                first = dict(body)
            chunk = body.get("channels") or []
            rows.extend(chunk)
            total = int(body.get("total") or 0)
            offset += len(chunk)
            if offset >= total:
                break
            if not chunk:
                break  # el daemon no avanza: no insistir
        out = dict(first)
        out["channels"] = rows
        out["count"] = len(rows)
        out["offset"] = 0
        out["truncated"] = offset < int(out.get("total") or 0)
        return 200, out

    def groups(self, target_id: str) -> tuple[int, dict]:
        return self.get(f"/targets/{target_id}/groups")

    def group_fields(self, target_id: str) -> tuple[int, dict]:
        return self.get(f"/targets/{target_id}/group-fields")

    def set_mappings(self, target_id: str, mapping: dict,
                     reload: bool = True) -> tuple[int, dict]:
        """Un mapeo suelto (para lotes, usa ``put`` directamente)."""
        return self.put(f"/targets/{target_id}/mappings",
                        {"mappings": [mapping], "reload": reload})

    def preview(self, target_id: str, rules: list[str]) -> tuple[int, dict]:
        """Vista previa de canales con esas reglas (no persiste nada)."""
        return self.post(f"/targets/{target_id}/preview", {"rules": rules})

    def refresh_epg(self, target_id: str) -> tuple[int, dict]:
        """Vuelve a leer las guías del target (descarga): espera al pipeline."""
        return self.post(f"/targets/{target_id}/refresh-epg",
                         timeout=PIPELINE_TIMEOUT)

    def run(self, target_id: str) -> tuple[int, dict]:
        """Ejecuta el pipeline. Espera larga: es la llamada lenta del guardado."""
        return self.post(f"/targets/{target_id}/run", timeout=PIPELINE_TIMEOUT)

    def set_settings(self, target_id: str, **changes) -> tuple[int, dict]:
        return self.put(f"/targets/{target_id}/settings", changes)
