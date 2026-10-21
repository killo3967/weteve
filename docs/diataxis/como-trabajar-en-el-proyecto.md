# Cómo trabajar en el proyecto

## Ciclo corto

```bat
.venv\Scripts\python.exe -m pytest -q          :: 982 tests, ~100 s
.venv\Scripts\python.exe -m ruff check src tests scripts main.py daemon.py
.venv\Scripts\python.exe -m mypy
```

Los tres tienen que estar en verde **antes** de dar algo por terminado. Los e2e
levantan un demonio real en un puerto efímero; si fallan por tiempo, se repiten
antes de tocar nada.

## Diagnóstico

```bat
.venv\Scripts\python.exe daemon.py status      :: exit 0 = vivo
.venv\Scripts\python.exe daemon.py stop
.venv\Scripts\python.exe scripts\healthcheck.py   :: contra un demonio vivo, manual
```

Logs: `logs/weteve-daemon.log` (demonio) y `logs/weteve.log` (GUI). El demonio
registra cada petición HTTP y cada recarga con sus tiempos.

## Añadir una ruta REST

1. Método en `TargetService` (`src/application/services/daemon/service.py`); no
   meter reglas en el adaptador.
2. Ruta en `RestApi.handle` (`src/infrastructure/web/rest_api.py`), traduciendo
   JSON a la llamada del servicio.
3. Cliente en `ApiClient` si la GUI la va a usar.
4. Test en `tests/test_rest_api.py` y, si cambia el contrato, en los e2e.

## Añadir una herramienta MCP

1. Función en `build_tool_registry` (`mcp_tools.py`), fina: solo llama al
   servicio.
2. Entrada en el diccionario con `description`, `schema` y `handler`.
3. Actualizar `EXPECTED_TOOLS` en `tests/e2e_support.py` y el recuento de
   `docs/daemon.md` (hoy **24**).

## Convenciones que evitan bugs ya vistos

- **Un escritor por fichero**: `daemon.toml` lo escribe el demonio, `weteve.ini`
  la GUI. La GUI escribe el `.toml` a través de la API, nunca directamente.
- **La GUI no calcula**: lee del snapshot del demonio. Si un cambio tiene que
  verse en la ventana, el demonio sube la `revision` del perfil.
- **Dos nombres por canal**: el interno (lista M3U) manda en dedupe y calidad; el
  mostrado es solo cosmético. No se edita el interno.
- **Nada de fuzzy puro**: el emparejamiento exige sufijo numérico igual y margen
  sobre el segundo candidato. Los mapeos dudosos se confirman a mano.
- **El puerto no es un candado**: la instancia única se garantiza con
  `weteve.lock` + `weteve.pid` antes de leer la configuración.
- **Fail closed** al guardar: validaciones en el servicio (dial repetido, logo
  inexistente, token obligatorio fuera de loopback), no solo en la GUI.

## Deuda conocida

- `config.ini`, `config_store.py`, `persistencia.py` y `sources_store.py` son la
  capa legada de migración; el código nuevo no debe escribir INI.
- 8 `except …: pass` en `src` (casi todos `OSError` con motivo).
- `scripts/healthcheck.py` no está en CI: necesita un demonio vivo.
- La ventana tiene deuda de tipos Qt (`header()`/`item()` devuelven `Optional`).
