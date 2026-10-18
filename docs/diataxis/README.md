# Documentación de weteve

Gestor de listas IPTV: toma una o varias M3U, aplica filtros, asocia guía EPG y
sirve el resultado como `playlist.m3u` + `epg.xml`. **El demonio es el motor; la
GUI es un frontend.**

Esta carpeta es la documentación canónica derivada (Diataxis). Los documentos
originales de `docs/` se conservan como histórico.

## Qué leer según lo que necesites

| Quiero… | Documento | Tipo |
|---|---|---|
| Poner weteve en marcha desde cero | [`tutorial-puesta-en-marcha.md`](tutorial-puesta-en-marcha.md) | Tutorial |
| Trabajar en el código (tests, lint, CI, añadir endpoints) | [`como-trabajar-en-el-proyecto.md`](como-trabajar-en-el-proyecto.md) | How-to |
| Consultar la API REST, MCP, control o playlist | [`referencia-api.md`](referencia-api.md) | Referencia |
| Saber qué clave va en `config.ini` | [`referencia-configuracion.md`](referencia-configuracion.md) | Referencia |
| Entender por qué está montado así y qué deuda queda | [`explicacion-arquitectura.md`](explicacion-arquitectura.md) | Explicación |

## Estado verificado (2026-10-18)

| Comprobación | Resultado |
|---|---|
| `pytest -q` | 681 pasan, 6 `network` deseleccionados (~90 s) |
| `ruff check .` | sin hallazgos |
| `mypy` | sin errores en 47 ficheros de código |
| `scripts/healthcheck.py` contra daemon real | 9 OK, 0 FAIL |
| Daemon con la config real | 85 canales servidos en 9999, 8 s de arranque |

E2E: `tests/test_daemon_e2e_api.py`, `test_daemon_e2e_playlist.py`,
`test_daemon_e2e_mcp.py` y `test_daemon_e2e_tools.py` levantan un daemon real con
puertos efímeros y no tocan tu `config.ini`.

## Mapa rápido del repositorio

```
src/domain/                  entidades y puertos (sin dependencias)
src/application/             motor: pipeline, filtros, EPG, servicio de targets
src/application/services/daemon/  servicio de targets, supervisor, store, REST y MCP
src/application/services/list_manager/  pipeline, filtros, EPG, M3U/XMLTV de salida
src/infrastructure/ui/       GUI Qt (frontend: solo habla con la API)
src/infrastructure/web/      servidores HTTP (playlist, control, transporte MCP)
src/infrastructure/client/   clientes (config, catálogo, API, daemon)
daemon.py                    arranque del demonio y CLI
main.py                      arranque de la GUI
scripts/healthcheck.py       diagnóstico de puertos, MCP, playlist y fuentes
docs/daemon.md               notas operativas del demonio (histórico, al día)
docs/list-manager.md         especificación funcional original (histórico)
```
