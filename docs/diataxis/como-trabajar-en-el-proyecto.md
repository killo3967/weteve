# Cómo trabajar en el proyecto

**Respuesta corta**: todo se valida con tres comandos y la suite no necesita red.

```powershell
.venv\Scripts\activate
ruff check .          # estilo y errores (ruleset en pyproject)
mypy                  # tipos (files/explicit_package_bases en pyproject)
pytest -q             # 681 tests, ~90 s; los `network` quedan fuera
```

La configuración de las tres herramientas vive en `pyproject.toml`. CI
(`.github/workflows/ci.yml`) ejecuta exactamente eso, más `compileall`, en
Windows/Python 3.11 con `QT_QPA_PLATFORM=offscreen`.

## Tests

| Comando | Qué cubre |
|---|---|
| `pytest -q` | todo lo que no sale a internet |
| `pytest -m network -q` | descargas reales de las fuentes URL de `config.ini` (6 tests); `abilio` (`http://`) y el EPG de Doble_M (>20 MB) los hacen fallar por datos, no por código |
| `pytest tests/test_daemon_e2e_api.py tests/test_daemon_e2e_playlist.py tests/test_daemon_e2e_mcp.py -q` | e2e (~30 s): daemon real, puertos efímeros, fixtures M3U/XMLTV |
| `pytest tests/test_ui_*.py -q` | GUI con dobles (`FakeApi`, `DaemonClient._request` parcheado) |

Reglas para añadir tests:

1. Los tests de UI **no** tocan red: `tests/conftest.py` ya inyecta `FakeApi` y
   bloquea el panel del demonio en todo `test_ui_*`.
2. No escribas en el `config.ini` del repo: usa `tmp_path` o `tests/e2e_support.start()`.
3. Para un endpoint nuevo, cubre tres niveles: servicio (`test_daemon_service`),
   adaptador en seco (`test_rest_api`) y, si es importante, e2e.

## Diagnóstico

```powershell
python daemon.py status          # ¿vivo? ¿qué targets sirve?
python scripts/healthcheck.py    # control, MCP (23 tools), targets, playlist, EPG, cartera
powershell -c "Get-Content logs\weteve-daemon.log -Tail 50 -Wait"
```

`healthcheck.py` acepta `--config C:/ruta/config.ini` y devuelve exit code 1 si
hay algún `FAIL`.

## Añadir una ruta REST

1. Lógica en `TargetService` (`src/application/services/daemon/service.py`).
2. Adaptador en `src/infrastructure/web/rest_api.py` (`_read` o `_write`).
3. Atajo en `ApiClient` (`src/infrastructure/client/api_client.py`) si la usa la GUI.
4. Tests: servicio + `tests/test_rest_api.py` + e2e si cambia el contrato.
5. Documenta en [`referencia-api.md`](referencia-api.md).

`_write` acepta `"reload": false` por diseño: la GUI manda un lote y ejecuta una
sola vez con `POST /targets/{id}/run`. Respeta esa regla para no multiplicar
pipelines de 3-8 s.

## Añadir una herramienta MCP

1. Handler en `src/application/services/daemon/mcp_tools.py` que **delegue** en
   `TargetService` (no dupliques lógica ni escribas el fichero).
2. Schema en el diccionario `return { ... }` del final.
3. `tests/test_daemon_mcp_tools.py` (registro) y `tests/e2e_support.py`
   (`EXPECTED_TOOLS`) si el set cambia.
4. Documenta en [`referencia-api.md`](referencia-api.md).

## Convenciones que evitan bugs ya vistos

- **Un solo escritor**: solo el demonio escribe `config.ini`
  (`config_store`). No añadas `save_config`/`save_catalog` en la UI.
- **Lecturas sin trabajo**: los endpoints de lectura salen del snapshot del
  supervisor; no ejecutan el pipeline (`reload`/`run` sí).
- **Cierra los sockets con margen**: tras escribir una respuesta HTTP, deja
  ~20 ms (`time.sleep(0.02)`) antes de que el servidor cierre el socket. En el
  loopback de Windows, cerrar con datos recién escritos provoca
  `ConnectionResetError` (~50 % medido). Está aplicado en `playlist_server` y en
  `mcp_transport`; si añades un servidor HTTP, haz lo mismo.
- **Errores de conexión**: `DaemonClient` y `ApiClient` no reintentan un
  rechazo ni un timeout (cuestan ~2 s en esta máquina) y, tras un fallo, no
  vuelven a intentarlo durante 5 s. Es lo que permite abrir la GUI con el
  demonio parado en ~2 s en vez de 90.
- **Caché de catálogo en la GUI**: `available_groups()`/`available_group_fields()`
  se cachean por diálogo; el widget de reglas las pide por cada fila. Si añades
  otra consulta "por fila", cachea o difiere o volverás a bloquear el arranque.

## Deuda conocida

- `mypy` ignora dos módulos Qt heredados (`source_catalog_widget`,
  `log_tail_widget`) por `findChildren()`/`header()` devolviendo `Optional`.
  Quitarlos de `[[tool.mypy.overrides]]` es el siguiente paso.
- No hay empaquetado (`[project]` en `pyproject.toml`): el código se importa
  como `src.*` desde la raíz.
- `scripts/healthcheck.py` no está en CI (necesita un daemon vivo).
