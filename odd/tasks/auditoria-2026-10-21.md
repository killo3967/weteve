# ODD Task: auditoria-2026-10-21

> Documento único de feature (contrato ODD de gentle-pi: `odd/tasks/<feature-name>.md`).
> Espejo de recuperación en Engram: topic `odd/auditoria-2026-10-21/tasks`.
> Estado: **ABIERTO** (2026-10-21). Origen: `docs/forensic-audit/2026-10-21.md`
> (auditoría forensic con 982 tests, ruff y mypy en verde; sin bugs abiertos).

## Estado actual

La auditoría no encontró nada roto: el programa funciona y la suite está en
verde. Lo pendiente es **deuda** (duplicidad de claves, silencios, capa legada)
y **mejoras** (rendimiento de la tabla, vigilancia de la fuente, documentación
canónica). Cada punto de abajo es una tarea con su criterio de aceptación.

## Objective

Cerrar la deuda que la auditoría del 2026-10-21 dejó identificada, empezando
por la que tiene impacto funcional (duplicidad `overrides`/fichas) y siguiendo
por los silencios y la experiencia de la tabla de mapeado.

## Problem

- Conviven dos formas de guardar lo mismo: `epg_overrides`/`disabled_channels`
  por url y las fichas por identidad. Esa duplicidad es la raíz del desorden de
  nombres y de tener 226 mapeos para 133 canales.
- Hay fallos que se tragan sin dejar rastro (`except: pass`).
- La tabla de mapeado se repinta entera y salta al principio en cada refresco.
- La documentación canónica describe el pipeline y la numeración anteriores.

## Why

Todo lo anterior ya está pagado una vez (diagnóstico + informe). Dejarlo escrito
como tareas evita volver a descubrirlo y permite atacarlo por unidades pequeñas.

## Scope

**Incluye:** las 11 tareas de abajo.

**No incluye:** rediseño de la GUI, cambios en el pipeline de matching ni
migraciones de datos del proveedor.

## Tasks

Cada tarea se cierra cuando su criterio de aceptación se cumple **y** la suite
(1003 tests al cierre del 2026-10-22), ruff y mypy siguen en verde.

### T01 — Revisar los `except …: pass` silenciosos

- [x] Estado: HECHO (2026-10-22) · Severidad: WARNING
  Los 8 bloques desnudos llevan ahora comentario que justifica el silencio
  (`single_instance.py` ×2, `logo_store.py`, `logo_cache_service.py` ×2,
  `config_store.py`, `daemon_launcher.py`, `playlist_server.py`).
  Evidencia: verificación AST 0 desnudos, ruff/mypy/982 tests en verde.
- Qué: 8 bloques de `src/` que se tragan la excepción sin log ni contexto.
- Dónde: `infrastructure/client/single_instance.py`, `list_manager/logo_store.py`,
  `ui/logo_cache_service.py`, `client/config_store.py`, `client/daemon_launcher.py`.
- Aceptación: cada bloque o lleva `logger.debug/warning` con el motivo, o un
  comentario que justifique el silencio. Sin `pass` desnudo en rutas de error.
- Evidencia: auditoría §Deuda.

### T02 — Poner fecha a la capa legada de `config.ini`

- [x] Estado: HECHO (2026-10-22) · Severidad: WARNING
  **Decisión: congelar, no retirar.** El INI viejo se queda solo para
  `python daemon.py migrate-config` (mantener indefinidamente: una migración no
  caduca). Nada del flujo normal lo escribe ni lo lee; el reparto vigente es
  `daemon.toml` (datos, lo escribe el demonio) + `weteve.ini` (arranque, lo
  escribe la GUI).
  Código: docstrings fechados en `config_store.py`, `persistencia.py` y
  `sources_store.py`; eliminados los dos escritores muertos del formato viejo
  (`write_target`, `write_sources`) con sus ayudantes (`selection_ids`,
  `_rules_str`, prefijos); cadenas `config.ini` corregidas en
  `daemon/service.py`, `control_api.py`, `mcp_tools.py`, `models.py`,
  `source_catalog.py`, `list_manager_dialog.py`, `source_catalog_widget.py`,
  `status_panel.py`, `daemon.py`, `supervisor.py` y `pyproject.toml`.
  Ejemplos: `config.toml.example` → `daemon.toml.example` (datos, con el
  encabezado correcto), `daemon.toml.example` → `weteve.ini.example` (arranque,
  ahora en INI como el real), retirado `config.ini.example`.
  Docs: `README.md`, `docs/daemon.md`, `docs/diataxis/referencia-configuracion.md`
  (fecha de congelación y ejemplos), `docs/diataxis/como-trabajar-en-el-proyecto.md`
  y `EXTRACCION.md` marcado como histórico.
  Evidencia: ruff, mypy (141 ficheros) y 982 tests en verde; cero referencias a
  `config.ini` en `src/` salvo las de la propia migración.
- Qué: `config.ini`, `config_store.py`, `persistencia.py`, `sources_store.py` son
  el formato viejo; 12 documentos lo siguen presentando como fuente de verdad.
- Dónde: los tres módulos, `docs/` y `README.md`.
- Aceptación: decisión escrita (retirar en N o mantener solo para
  `migrate-config`) y documentación que diga cuál es el formato actual.
- Evidencia: auditoría §Deuda.

### T03 — Decidir qué pasa con `scripts/healthcheck.py`

- [x] Estado: HECHO (2026-10-22) · Severidad: INFO
  **Decisión: retirar.** Se borra `scripts/healthcheck.py` (y la carpeta).
  Motivo: su lista de herramientas ya estaba desfasada (23 de 24, sin
  `modify_channels`), sus rutas eran las viejas (`/weteve/{id}/playlist.m3u`,
  `/epg.xml`) y leía `config.ini`; pero sobre todo **duplicaba** lo que ya
  cubren los e2e, que además tienen la lista autoritativa en
  `tests/e2e_support.py::EXPECTED_TOOLS` y comprueban MCP, rutas y formatos con
  HTTP real. En CI no puede entrar sin levantar un demonio con fixtures, cosa
  que la suite ya hace.
  Docs: ruta quitada del mapa de `docs/diataxis/README.md`, `compileall` de
  `.github/workflows/ci.yml` sin `scripts`, y en
  `docs/diataxis/como-trabajar-en-el-proyecto.md` la deuda pasa a explicar cómo
  comprobar a mano (`daemon.py run` + `curl`).
- Qué: no lo ejecuta nadie, dice «23 tools» y acepta `--config config.ini`.
- Dónde: `scripts/healthcheck.py`, `docs/diataxis/como-trabajar-en-el-proyecto.md`.
- Aceptación: o se actualiza (24 herramientas, rutas `/m3u`/`/xmltv`, config TOML)
  y entra en CI, o se retira del repo y de la documentación.

### T04 — Renombrar la constante `TODOS`

- [x] Estado: HECHO (2026-10-22) · Severidad: INFO
  `TODOS` → `ANY_IP` en `src/infrastructure/client/net_utils.py` (constante y
  sus dos usos: `_NO_NAVEGABLES` y `host_options`), con comentario que aclara
  que es un host de escucha, no un destino. Evidencia: `grep -E '\bTODO\b|\bFIXME\b'`
  sobre `src/` devuelve cero coincidencias; la única aparición de «TODOS» que
  queda es prosa en español de un test. ruff, mypy y 982 tests en verde.
- Qué: `TODOS = "0.0.0.0"` en `net_utils.py` colisiona con la búsqueda de
  marcadores TODO/FIXME.
- Aceptación: nombre que no sea ambiguo (`ANY_IP`, `TODAS_INTERFACES`), tests en
  verde y cero falsos positivos al buscar marcadores.

### T05 — Migrar `epg_overrides` y `disabled_channels` a fichas

- [x] Estado: HECHO (2026-10-22) · Severidad: ALTA (funcional)
  **Una sola clave: la identidad del canal.** `ChannelProfile` gana `epg` y
  `enabled`; el pipeline los lee (la ficha manda sobre el mapeo viejo por url y
  sobre el parecido automático) y `_apagar_por_ficha` aplica el apagado después
  de resolver la identidad, que es cuando ya se sabe de qué canal se habla.
  El demonio traduce en el guardado: `set_mappings`/`set_disabled` reciben urls
  (lo que ven la UI y el MCP) y escriben en la ficha usando el mapa url →
  identidad del último pipeline; lo que no sepa traducir se queda en el formato
  viejo, que se sigue leyendo. `serialize_target` hace el camino inverso para
  publicar lo de siempre, y la UI parte de esa lista completa (no de la página
  cargada) para no guardar nunca de menos.
  **Ensayo con el perfil real** (copia del `daemon.toml`, snapshot de 855 filas
  del demonio en marcha): 41 apagados → 41 fichas con `enabled = false`,
  residuo 0, pérdidas 0; fichero 47 764 → 43 474 bytes.
  **Hallazgo importante**: los **226 mapeos por url son huérfanos** — apuntan al
  host viejo del proveedor (`host antiguo del proveedor`) y la lista actual usa otro
  (`host nuevo del mismo proveedor`). Ninguno casa con un canal actual: llevan tiempo sin
  aplicarse y el pipeline resolvía esos canales por parecido automático. No se
  borran ni se reanclan a ciegas (201 de ellos comparten id de canal en la ruta,
  pero reanclar por número es adivinar): se conservan tal cual hasta que el
  usuario decida. Las decisiones nuevas ya van a fichas.
  Tests: `TestGuiaYApagadoEnLaFicha` (7), `TestGuardarPorIdentidad` (7),
  `TestFichaConGuiaYApagado` (3) y `TestDecisionesEnFichas` (2).
- Qué: hoy el EPG asignado y el apagado manual van por url y el dial/nombre/logo
  por identidad. Son dos claves para el mismo canal.
- Dónde: `models.py` (`PipelineTarget`), `config_toml.py`, `service.py`,
  `pipeline.py`, `supervisor.py` (mapping-candidates), UI de mapeado y ~19 tests.
- Aceptación: una sola clave (identidad) para todo lo que el usuario fija;
  lectura del formato viejo con plegado y escritura solo del nuevo; los 226
  mapeos de un perfil real se reducen a una ficha por canal sin perder ninguno.
- Evidencia: auditoría §Mejorar 1; decisión previa de no mezclarlo con la ficha.

### T06 — Repintar la tabla sin perder scroll ni selección

- [x] Estado: HECHO (2026-10-22) · Severidad: INFO
  `EpgMappingWidget._capture_view` guarda la **identidad** de la fila superior y
  de las seleccionadas antes de vaciar la tabla (el número de fila no sirve: el
  orden lo decide el demonio por dial), y `_restore_view` las devuelve después
  del repintado con `scrollToItem(PositionAtTop)` y el modelo de selección.
  Tests: `test_ui_mapping_rows.py::TestLaVistaSobreviveAlRefresco` (fila nueva
  delante y orden distinto incluidos).
- Qué: `_refresh_mapping` vacía y repuebla la tabla; se pierde la posición.
- Dónde: `ui/list_manager_dialog.py`.
- Aceptación: tras un refresco automático (revisión) o manual se conserva la
  fila superior visible y las filas seleccionadas por identidad.

### T07 — Paginar `mapping-candidates` por lo visible

- [x] Estado: HECHO (2026-10-22) · Severidad: INFO
  La tabla pide **una página** de `MAPPING_PAGE = 50` filas con `limit`/`offset`
  y el botón «Cargar más (50 de 300)» dobla la página; el total llega en la
  misma respuesta (`total`), sin descargar el catálogo. Se conserva el tamaño
  de página entre refrescos y `get_disabled()` ya no pierde los canales
  apagados que no están en la página cargada (se reactivarían al guardar).
  `api_client.all_mapping_candidates` quedó sin uso y se retiró.
  Tests: `TestPaginacionDeLaTabla` (página, botón, sin pendientes, apagado
  fuera de página).
- Qué: cada refresco trae 855 filas (~200 KB) aunque solo se vean ~30.
- Dónde: `supervisor.mapping_candidates`, `api_client.all_mapping_candidates`, UI.
- Aceptación: la tabla pide páginas (o un límite por vista) y el total se conoce
  sin descargar todas las filas.

### T08 — Avisar del truncamiento por `_MAX_LIMIT`

- [x] Estado: HECHO (2026-10-22) · Severidad: INFO
  El supervisor arma todas las páginas de lectura con `_pagina()`, que añade
  `truncated: offset + count < total` (mapping-candidates, channels,
  epg-channels y catalog). La UI distingue los dos casos: página incompleta
  normal (el botón dice «N de M» y no avisa) y respuesta recortada por el
  demonio (pide más de lo que recibe → «catálogo incompleto»).
  Tests: `TestCatalogoIncompleto` (aviso al recortar; sin aviso con página
  llena).
- Qué: el límite de 1000 por página deja fuera filas en silencio (hoy 855).
- Aceptación: si una respuesta se trunca, viaja un campo `truncated` (ya existe
  en otros endpoints) y la UI lo dice.

### T09 — Vigilar la línea del proveedor

- [x] Estado: HECHO (2026-10-22) · Severidad: INFO
  `list_manager/stream_probe.py`: `StreamProbe` muestrea 3 canales repartidos
  (no los tres primeros), `HEAD` con caída a `GET Range: bytes=0-0` para los
  proveedores que rechazan `HEAD`, timeout 5 s y caché de 5 min por perfil. El
  sondeo corre en un hilo: `/status` devuelve lo último sabido y nunca espera al
  proveedor. `TargetSupervisor.get_status()` publica `streams: {checked, ok,
  pending}` por perfil y la pestaña «Estado» pinta «N de M streams responden»
  con aviso (🟡) si baja del 50 % (`UMBRAL_STREAMS`).
  Tests: `tests/test_stream_probe.py` (muestreo, caché, TTL, fallo, trabajador)
  y `TestStreamsDelProveedor` en `test_ui_status_panel.py`.
  Gotcha encontrado y corregido: el camino síncrono (checker inyectado) se
  llamaba con el candado tomado y `_run` lo vuelve a tomar — cerrojo no
  reentrante, se colgaba. Ahora el sondeo se ejecuta fuera del candado.
- Qué: la lista murió por una rotación de host; hoy es URL con `refresh_min`,
  pero nada avisa si los streams dejan de responder.
- Aceptación: el estado del perfil expone «N de M streams responden» (muestreo
  barato, p. ej. 3 canales) y la UI avisa si baja de un umbral.

### T10 — Actualizar la documentación canónica

- [x] Estado: HECHO (2026-10-22) · Severidad: INFO
  `docs/list-manager.md` abre con «Estado actual (2026-10-22)»: demonio como
  motor, REST, los dos ficheros de config y el pipeline real (parseo → filtros →
  cuatro fases de match → identidad canónica → dedupe por calidad → salida);
  el resto queda marcado como especificación original (y su bloque de
  `config.ini` como histórico). `docs/especificacion-gestor-listas.md` se
  reescribió: sin «IPTVViewer», cartera `[[sources]]`, las cuatro fases, rutas
  `/m3u` y `/xmltv` en el puerto único y `weteve.ini` como sitio del alcance.
  `docs/daemon.md` y `README.md` al día en el cierre de T02.
- Qué: `docs/list-manager.md` y `docs/especificacion-gestor-listas.md` describen
  el pipeline y la numeración anteriores (rutas ya corregidas).
- Aceptación: ambos documentos cuentan el pipeline actual (4 fases de match,
  identidad canónica, dedupe, ficha) o enlazan a `docs/diataxis/` como fuente.

### T11 — Decidir el versionado de los logos manuales

- [x] Estado: HECHO (2026-10-22) · Severidad: INFO
  **Decisión: manifiesto versionado, imágenes no.** `assets/logos/` sigue
  ignorado (se regenera y engorda sola), y `assets/logos-manual.json` — fuera de
  esa carpeta, así que sí lo versiona git — registra canal, fichero y origen de
  los logos elegidos a mano (hoy: Odisea, Runtime Cine, Nature Time) con el
  procedimiento de recuperación tras un clon. Documentado en
  `docs/diataxis/como-trabajar-en-el-proyecto.md` y
  `docs/diataxis/referencia-configuracion.md`.
- Qué: `assets/logos/` está ignorado; los logos elegidos a mano no viajan con el
  repo (hoy 3 nuevos: Odisea, Runtime Cine, Nature Time).
- Aceptación: o se versiona un manifiesto `assets/logos/manual.json` (nombre →
  origen), o se documenta que se pierden al clonar y cómo recuperarlos.

## Test Plan

- Las tareas de código (T01, T04, T05, T06, T08, T09) añaden test propio; T05
  incluye el plegado del formato viejo y una prueba de no-pérdida con el target
  real.
- Cada cierre ejecuta `pytest -q`, `ruff check` y `mypy`.

## Acceptance Criteria

- [x] Las 11 tareas están cerradas o explícitamente descartadas con motivo:
  T01-T04 y T06-T11 (2026-10-22, commit `8241b1d`) y T05 después, sola.
- [x] Suite (1022 tests), ruff y mypy en verde en cada cierre.
- [ ] `docs/forensic-audit/2026-10-21.md` queda como referencia histórica; este
  documento es el que manda para el trabajo pendiente.

## Risks / Open Questions

- **T05 es la única cara**: toca 7 módulos y ~19 tests; el resto son cambios
  locales. Si se hace, va sola, con su rama y su verificación completa.
- **T09** puede generar falsos negativos si el proveedor limita peticiones:
  muestrear pocos canales y con caché.
- **Seguridad (aceptada)**: el token viaja en claro por HTTP dentro de la LAN.
  Si algún día se abre fuera, hace falta TLS o un proxy delante.
- Orden seguido: T01 → T02 → T03 → T04 → T06 → T07 → T08 → T09 → T10 → T11 →
  T05 (la única con riesgo de datos, con respaldo previo del `daemon.toml` y
  ensayo sobre copia).
