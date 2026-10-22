# ODD Task: list-manager

> Documento único de feature (contrato ODD de gentle-pi: `odd/tasks/<feature-name>.md`).
> Espejo de recuperación en Engram: topic `odd/list-manager/tasks`.
> Estado: **IMPLEMENTADO** (2026-10-08). Pasos 1-7 del plan completos; smoke test HTTP verificado en subproceso (m3u 200, epg 200, 404 en rutas desconocidas). Pendiente: validación manual del usuario con una lista real y un reproductor.

## Estado actual (2026-10-22)

La arquitectura cambió: **el demonio es el motor y la UI es un frontend**.

- El pipeline, la cartera de fuentes y el servidor de playlist viven en el
  demonio (`TargetService` + `TargetSupervisor`); la UI no ejecuta nada de eso.
- La UI habla por **REST v1** (`/api/v1`, mismo puerto que MCP) y escribe por
  ahí: `PUT /sources`, `PUT /targets/{id}/sources|settings|rules|mappings|disabled`
  y un único `POST /targets/{id}/run` al final del lote.
- Dos ficheros, un escritor cada uno: `daemon.toml` (datos: cartera y perfiles)
  lo escribe el demonio; `weteve.ini` (`[DAEMON]`: host, puerto, token y
  arranque) lo escribe la GUI. `config.ini` quedó como formato viejo, solo de
  migración (`python daemon.py migrate-config`).
- Detalle operativo y de protocolo: `docs/daemon.md`. Configuración:
  `docs/diataxis/referencia-configuracion.md`.

### Lo que hace el pipeline hoy

1. **Parseo** de la unión de las listas M3U activas del perfil.
2. **Filtros** por campos, en orden (`group-title`, `custom-filter`, `tvg-group`,
   `extgrp`, `source`), combinables con `and`/`or`.
3. **Asociación EPG en cuatro fases** (`epg_matcher.py`): `tvg-id` exacto →
   nombre normalizado exacto → nombre base sin etiqueta de calidad (`LA 1 HD`
   es `LA 1`) → parecido por palabras de marca, con dos reglas duras: el sufijo
   numérico tiene que coincidir y el ganador necesita margen sobre el segundo.
4. **Identidad canónica** por canal (el código de guía, o `url-<hash>` /
   `name-<base>` si no lo hay): es la clave de la **ficha** (dial, nombre
   visible, logo) y del **dedupe**.
5. **Dedupe por calidad**: de las variantes del mismo canal se sirve una, según
   `quality_priority`.
6. **Salida**: M3U con dial y `url-tvg` + XMLTV solo con los canales servidos.
   Un canal sin guía no se sirve si el perfil tiene fuente EPG; se le asigna
   `Dummy 1h` cuando tiene dial en la ficha o `serve_without_epg` activo.

El resto de este documento es la **especificación original** (2026-10-08): vale
como contrato funcional (filtros, matching, mini-web), no como descripción del
código actual. Las partes que hablaban de un «asistente de pipeline» dentro del
diálogo están superadas.

## Objective

Un gestor de listas que integra la funcionalidad esencial de este tipo de
herramientas: tomar una o varias listas M3U, aplicarles
un motor de filtros, asociarlas a guías EPG (XMLTV) por coincidencia automática
con corrección manual, y **servir el resultado como una mini-web local** que expone
la lista filtrada (`.m3u`) y la guía regenerada (`.xml`) para consumo por el propio
la aplicación anfitriona o por reproductores externos.

## Problem

El programa anfitrión ya descargaba y parseaba M3U y XMLTV por fuente, pero:

- El único "filtro" es un campo de texto plano por fuente que se evalúa de forma
  opaca dentro del parseo; no hay reglas combinables, ni incluir/excluir, ni
  vista previa del resultado.
- La asociación M3U↔EPG depende del `tvg-id` que traiga la lista; cuando falla,
  el usuario no tiene forma de corregirla.
- No hay manera de consumir el resultado filtrado desde fuera de la aplicación:
  las URLs de los streams quedan encapsuladas en la app.

Los gestores de listas de referencia resuelven exactamente esto (filtros, mapeo
EPG, exportación web),
pero son aplicaciones Go separadas con su propia UI y su propio ciclo de vida.

## Why

Unificar la gestión de listas en la aplicación evita mantener dos herramientas
(la app y un gestor externo), reutiliza los parsers existentes (`FileM3URepository`,
`XMLTVRepository`) y da al usuario control visible sobre qué canales consume y con
qué guía.

## Scope

**Incluye:**
- Diálogo "Gestor de listas" con pestañas: Listas IPTV, Listas EPG, Filtros (+ vista
  previa), Mapeado EPG, Configuración y Logs. El servicio web se arranca y se para
  desde Configuración, pero **lo sirve el demonio**.
- Pipeline de procesamiento (Qt-free, testeable): parseo M3U → filtros → asociación
  EPG → generación de salida.
- Motor de filtros: la **UI** expone reglas simples (incluir/excluir por texto sobre
  grupo o nombre, combinables); el **modelo de dominio** persiste la sintaxis
  extendida por campos (`group-title` / `custom-filter`, `{incluir}` / `!{excluir}`,
  case-sensitive opcional) para crecer después sin migración.
- Asociación EPG automática: match por `tvg-id`, fallback por nombre normalizado
  (lowercase, sin acentos/puntuación, colapsado de espacios).
- Editor manual de asociación EPG: tabla de canales sin match con sugerencias del
  XMLTV para asignación manual.
- Mini-web local (solo `localhost` por defecto): `GET /m3u/weteve_<perfil>.m3u` (lista filtrada
  y mapeada) y `GET /epg.xml` (guía regenerada solo con canales activos).
- Persistencia en `config.ini`: nueva sección por objetivo de pipeline
  *(histórico: hoy los datos viven en `daemon.toml`)*.

**Excluye (decisiones de alcance 2026-10-08):**
- **No hay proxy de streams** (`/stream/{id}`): la m3u servida apunta a las URLs
  originales de los canales. La reproducción externa va directa al origen.
- **No emula sintonizadores de red** (descubrimiento ni lista de canales sintonizados).
- No hay buffer/re-streaming, ni imágen-caché de logos, ni usuarios/autenticación
  en la web (es local).
- No se toca el flujo actual de "Mis Listas" (selección de fuente activa, player):
  el gestor es ortogonal; puede usarse una fuente como objetivo del pipeline sin
  cambiar la fuente activa del reproductor.

## Non-Goals

- Compatible byte a byte con la salida de otro gestor de listas.
- Soportar las 100% de las excentricidades de M3U del ecosistema (se cubre lo que
  ya parsea `FileM3URepository`).
- Scheduler de auto-actualización de fuentes (queda para una fase futura; las
  fuentes se refrescan manualmente desde el gestor).

## Architecture

Capa nueva en `src/application/services/list_manager/` (Qt-free) + adaptadores en
`src/infrastructure/`:

```
src/application/services/list_manager/
├── __init__.py
├── models.py            # PipelineTarget, FilterRule, ChannelMapping, ServiceConfig
├── filter_engine.py     # aplica reglas sobre canales parseados
├── epg_matcher.py       # match automático tvg-id + nombre normalizado
├── pipeline.py          # orquesta: parse → filter → match → build outputs
└── playlist_builder.py  # genera M3U de salida y XMLTV de salida

src/infrastructure/
├── web/playlist_server.py   # http.server en hilo daemon, localhost-only
└── ui/list_manager_dialog.py# frontend: pestañas + cliente de la API del demonio
```

Reutiliza sin modificar: `FileM3URepository._parse_m3u`, `XMLTVRepository.load_epg`,
la estructura `[source.N]` de `main.py` (load/save config).

### Modelos de dominio (borrador)

```python
@dataclass(frozen=True)
class FilterRule:
    field: Literal["group-title", "custom-filter"]  # campos del modelo
    pattern: str          # texto simple en UI; patrón heredado si viene de config antigua
    exclude: bool
    case_sensitive: bool = False

@dataclass(frozen=True)
class PipelineTarget:
    name: str
    source_index: int          # referencia a [source.N] existente
    rules: list[FilterRule]
    epg_source_index: int | None  # referencia a la epg de una [source.N]
    epg_overrides: dict[str, str] # canal m3u (uuid) -> tvg-id xmltv (del editor manual)
    port: int = 8080
    enabled: bool = True

@dataclass(frozen=True)
class ServiceConfig:
    targets: list[PipelineTarget]
```

El wizard en 3 pasos mapea 1:1 sobre `PipelineTarget`; cada paso edita un campo.

### Filtros (modelo por campos, UI simple)

Sintaxis interna del `FilterRule.pattern` cuando `field="custom-filter"`: subcadena
sobre los atributos crudos del EXTINF. Semántica equivalente a `filterThisStream` de
El modelo de referencia define:

- Regla sin llaves: coincide si el patrón aparece en el grupo o el nombre.
- `{texto}`: incluir además los canales que cumplan `texto`.
- `!{texto}`: excluir los canales que cumplan `texto`.

La UI nunca pide esa sintaxis: ofrece filas "campo = [grupo|nombre] contains [texto]
→ incluir|excluir" y las traduce al modelo.

### Asociación EPG

1. Normalización de nombre: `NFKD` → quitar diacríticos → lowercase → quitar
   puntuación → colapsar espacios.
2. Match 1º por `tvg-id` exacto (canal vs canal del XMLTV).
3. Fallback por nombre normalizado exacto y por nombre base, sin la etiqueta de
   calidad —también cuando el proveedor la pega al nombre (`M+ LaLigaTVHD`,
   `M+ #VAMOSHD`)—.
4. Fallback por parecido de marca: se comparan las palabras significativas
   (las variantes del nombre del operador son la misma marca, el sufijo `TV` no
   distingue, `M+LCAMPEON4` es `M+ Liga de Campeones 4`, las anotaciones entre
   paréntesis se ignoran). Dos reglas duras: el sufijo numérico tiene que
   coincidir (`M+ LaLiga 2` no es `M+ LaLiga`) y el ganador necesita margen
   sobre el segundo candidato.
5. **Identidad del canal** (`canonical_id`): el id de la guía que publica dial y
   logo, resuelto aparte del emparejamiento. Manda para el número, el logo y
   para agrupar variantes, aunque la parrilla venga de otra guía: un mapeo
   manual a Koala ya no deja el canal sin dial. Dos nombres del mismo canal
   (`BeMad tv HD` y `BE MAD HD`) se sirven una sola vez.
6. Sin match: el canal **no se sirve** cuando el target tiene fuente EPG
   (`require_epg`), porque un canal sin guía es ruido en el reproductor. Se
   conserva en `PipelineResult.excluded_no_epg` para que el editor lo muestre y
   se pueda rescatar. Sin fuente EPG la regla no se aplica: no se puede exigir
   una guía que no existe.
7. Rescate desde el editor: desplegable con los canales de la guía (o texto
   libre para pegar un channel-id que la guía no anuncia), o `Dummy 1h` para
   generar una guía de relleno sintética (bloques de 1 h, 7 días, títulos con el
   nombre del canal). Las sugerencias por similitud
   (`EpgMatcher.suggestions()`, `difflib`) las expone el daemon como endpoint de
   solo lectura; la tabla de mapeo **no** las usa.
8. La corrección manual se persiste en `epg_overrides` (`url::canal-id`, pares
   separados por `;;`) y tiene prioridad sobre el automático en regeneraciones.
   `Dummy 1h` se persiste como id sintético `weteve-dummy-<hash>` derivado de la
   url, único por canal.
9. La etiqueta del desplegable se muestra sin el sufijo `.TV` (solo visual: lo
   persistido sigue siendo el channel-id real, p. ej. `La1.TV`). Si la guía
   trajera un canal llamado literalmente `Dummy 1h`, la opción sintética se
   retira y gana el canal real.

### Mini-web

- `http.server.ThreadingHTTPServer` en hilo daemon; bind `127.0.0.1` (configurable
  a `0.0.0.0` explícitamente si el usuario quiere LAN — warning en la UI).
- Endpoints: `GET /m3u/weteve_<perfil>.m3u` y `GET /xmltv/weteve_<perfil>.xml`
  si hay más de un target).
- La salida se **pre-genera** al arrancar el servicio y tras cada edición del
  target; los endpoints solo sirven el string cacheado (rápido, sin I/O por request).
- El contenido del EPG de salida solo incluye canales del target activo.
- Sin autenticación (local); log de requests a `logs/list_manager_web.log`.

## Data Model / Config

> **Histórico.** Este bloque describe el borrador de 2026-10-08 en `config.ini`.
> El esquema vigente es TOML en `daemon.toml`; ver
> `docs/diataxis/referencia-configuracion.md`.

`config.ini` (nuevo bloque, convivencia con `[source.N]`):

```ini
[listmanager.service]
enabled = False
port = 8080
bind = 127.0.0.1

[listmanager.target.0]
name = TV España filtrada
source = 0                  ; índice en [source.N]
epg_source = 0              ; índice de la fuente cuyo epg se usa
rules = group-title|Sports|include;custom-filter|!{XXX}|exclude
epg_overrides = <url>::<tvg_id_xmltv>;;<url>::<tvg_id>
disabled_channels = <url>||<url>   ; solo apagado manual del usuario
starting_number = 1               ; NO se persiste: ver nota abajo
```

> `starting_number` vive en el modelo (`PipelineTarget`) y en las herramientas
> MCP, pero ni `persistencia.py` ni `target_store.py` lo leen ni lo escriben: se
> reinicia a 1 en cada carga. Pendiente de decidir si persistirlo.

La casilla `Activo` de la tabla es derivada (sin guía asignada **o** apagado a
mano), pero `disabled_channels` guarda **únicamente** el apagado manual: si no,
un guardado convertaría cientos de canales «sin guía» en «apagados» y ya no se
podrían rescatar con `Dummy 1h`.

Migración: nada que migrar (sección nueva). Si no existe, el menú muestra el
wizard vacío.

## Implementation Plan

1. `models.py` + `filter_engine.py` con pruebas unitarias (semántica de campos).
2. `epg_matcher.py` con tests (normalización, fallback, sugerencias).
3. `pipeline.py` + `playlist_builder.py` con tests (fixtures M3U/XMLTV reales
   reducidos, incluido caso Pluto TV sin tvg-name).
4. `playlist_server.py` con tests (start/stop, endpoints, contenido cacheado).
5. `list_manager_dialog.py`: wizard 3 pasos + tabla de filtros + editor EPG.
6. Integración en `main_window.py` (submenú "Gestor de listas") y `main.py`
   (load/save `[listmanager.*]`).
7. Prueba manual: servir una lista real filtrada y cargarla en un reproductor.

## Test Plan

- Unitarios Qt-free para `filter_engine`, `epg_matcher`, `playlist_builder`
  (fixtures en `tests/fixtures/list_manager/`).
- `playlist_server` con `pytest` + servidor en puerto efímero (0).
- Integración UI con `pytest-qt`: apertura del wizard, persistencia de un target,
  acción de menú presente.
- Regresión: la suite existente (56+ tests) sigue en verde.

## Acceptance Criteria

- [ ] Submenú "Gestor de listas" visible bajo "Mis Listas" con wizard funcional.
- [ ] Un target con filtros produce una M3U filtrada verificable (nº de canales
      esperado en fixture).
- [ ] El EPG de salida contiene solo programas de canales del target, con los
      overrides manuales respetados.
- [ ] `http://127.0.0.1:<port>/m3u/weteve_0.m3u` y `/xmltv/weteve_0.xml` responden con el
      contenido regenerado y dejan de responder al parar el servicio.
- [ ] Config se persiste y sobrevive reinicio de la app.
- [ ] Suite completa en verde, ruff y mypy sin errores nuevos.

## Risks / Open Questions

- **Rendimiento con listas grandes** (Pluto 250+): el pipeline debe ser streaming
  o al menos O(n+m); medir con la lista real. Riesgo bajo.
- **Nombres duplicados en XMLTV**: el fallback por nombre puede asociar a un canal
  equivocado; mitigado por el editor manual (el automático solo es "primera
  sugerencia").
- **Puerto ocupado**: elegir puerto efímero alternativo y avisar en la UI si el
  configurado está en uso al arrancar el servicio.
- Pendiente decidir en implementación: si el target reutiliza la fuente activa del
  reproductor o siempre una copia independiente (se resuelve apuntando por índice,
  sin copiar datos).
