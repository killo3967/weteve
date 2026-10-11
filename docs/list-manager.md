# ODD Task: list-manager

> Documento único de feature (contrato ODD de gentle-pi: `odd/tasks/<feature-name>.md`).
> Espejo de recuperación en Engram: topic `odd/list-manager/tasks`.
> Estado: **IMPLEMENTADO** (2026-10-08). Pasos 1-7 del plan completos; smoke test HTTP verificado en subproceso (m3u 200, epg 200, 404 en rutas desconocidas). Pendiente: validación manual del usuario con una lista real y VLC.

## Objective

Nuevo submenú **"Gestor de listas"** bajo "Mis Listas" que integra la funcionalidad
esencial de xTeVe/Threadfin en IPTVViewer: tomar una o varias listas M3U, aplicarles
un motor de filtros, asociarlas a guías EPG (XMLTV) por coincidencia automática
con corrección manual, y **servir el resultado como una mini-web local** que expone
la lista filtrada (`.m3u`) y la guía regenerada (`.xml`) para consumo por el propio
IPTVViewer o por reproductores externos (VLC, Jellyfin, Plex-like).

## Problem

IPTVViewer ya descarga y parsea M3U y XMLTV por fuente (`[source.N]`), pero:

- El único "filtro" es un campo de texto plano por fuente que se evalúa de forma
  opaca dentro del parseo; no hay reglas combinables, ni incluir/excluir, ni
  vista previa del resultado.
- La asociación M3U↔EPG depende del `tvg-id` que traiga la lista; cuando falla,
  el usuario no tiene forma de corregirla.
- No hay manera de consumir el resultado filtrado desde fuera de la aplicación:
  las URLs de los streams quedan encapsuladas en la app.

xTeVe/Threadfin resuelven exactamente esto (filtros, mapeo EPG, exportación web),
pero son aplicaciones Go separadas con su propia UI y su propio ciclo de vida.

## Why

Unificar la gestión de listas en la aplicación evita mantener dos herramientas
(la app y un xTeVe externo), reutiliza los parsers existentes (`FileM3URepository`,
`XMLTVRepository`) y da al usuario control visible sobre qué canales consume y con
qué guía.

## Scope

**Incluye:**
- Submenú "Gestor de listas" con: editor de canal/objetivo (nuevo/editar/eliminar),
  asistente de pipeline en 3 pasos (Fuente → Filtros → EPG) y diálogo "Iniciar/Parar
  servicio web".
- Pipeline de procesamiento (Qt-free, testeable): parseo M3U → filtros → asociación
  EPG → generación de salida.
- Motor de filtros: la **UI** expone reglas simples (incluir/excluir por texto sobre
  grupo o nombre, combinables); el **modelo de dominio** persiste la sintaxis
  extendida de xTeVe (`group-title` / `custom-filter`, `{incluir}` / `!{excluir}`,
  case-sensitive opcional) para crecer después sin migración.
- Asociación EPG automática: match por `tvg-id`, fallback por nombre normalizado
  (lowercase, sin acentos/puntuación, colapsado de espacios).
- Editor manual de asociación EPG: tabla de canales sin match con sugerencias del
  XMLTV para asignación manual.
- Mini-web local (solo `localhost` por defecto): `GET /playlist.m3u` (lista filtrada
  y mapeada) y `GET /epg.xml` (guía regenerada solo con canales activos).
- Persistencia en `config.ini`: nueva sección por objetivo de pipeline.

**Excluye (decisiones de alcance 2026-10-08):**
- **No hay proxy de streams** (`/stream/{id}`): la m3u servida apunta a las URLs
  originales de los canales. La reproducción externa va directa al origen.
- **No hay emulación HDHR** (`discover.json` / `lineup.json`).
- No hay buffer/re-streaming, ni imágen-caché de logos, ni usuarios/autenticación
  en la web (es local).
- No se toca el flujo actual de "Mis Listas" (selección de fuente activa, player):
  el gestor es ortogonal; puede usarse una fuente como objetivo del pipeline sin
  cambiar la fuente activa del reproductor.

## Non-Goals

- Compatible byte a byte con la salida de xTeVe.
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
└── ui/list_manager_dialog.py# wizard 3 pasos + editor EPG + control servicio
```

Reutiliza sin modificar: `FileM3URepository._parse_m3u`, `XMLTVRepository.load_epg`,
la estructura `[source.N]` de `main.py` (load/save config).

### Modelos de dominio (borrador)

```python
@dataclass(frozen=True)
class FilterRule:
    field: Literal["group-title", "custom-filter"]  # xTeVe-compatible en el modelo
    pattern: str          # texto simple en UI; patrón xTeVe si viene de config antigua
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

### Filtros (modelo xTeVe, UI simple)

Sintaxis interna del `FilterRule.pattern` cuando `field="custom-filter"`: subcadena
sobre los atributos crudos del EXTINF. Semántica equivalente a `filterThisStream` de
xTeVe (`src/m3u.go`):

- Regla sin llaves: coincide si el patrón aparece en el grupo o el nombre.
- `{texto}`: incluir además los canales que cumplan `texto`.
- `!{texto}`: excluir los canales que cumplan `texto`.

La UI nunca pide esa sintaxis: ofrece filas "campo = [grupo|nombre] contains [texto]
→ incluir|excluir" y las traduce al modelo.

### Asociación EPG

1. Normalización de nombre: `NFKD` → quitar diacríticos → lowercase → quitar
   puntuación → colapsar espacios.
2. Match 1º por `tvg-id` exacto (canal vs canal del XMLTV).
3. Fallback por nombre normalizado exacto.
4. Sin match: canal queda sin EPG; aparece en el editor manual con hasta 5
   sugerencias por similitud (`difflib.SequenceMatcher`).
5. La corrección manual se persiste en `epg_overrides` y tiene prioridad sobre el
   automático en regeneraciones.

### Mini-web

- `http.server.ThreadingHTTPServer` en hilo daemon; bind `127.0.0.1` (configurable
  a `0.0.0.0` explícitamente si el usuario quiere LAN — warning en la UI).
- Endpoints: `GET /playlist.m3u` y `GET /epg.xml` (uno por target: `/t/<name>/...`
  si hay más de un target).
- La salida se **pre-genera** al arrancar el servicio y tras cada edición del
  target; los endpoints solo sirven el string cacheado (rápido, sin I/O por request).
- El contenido del EPG de salida solo incluye canales del target activo.
- Sin autenticación (local); log de requests a `logs/list_manager_web.log`.

## Data Model / Config

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
epg_overrides = <canal_uuid>:<tvg_id_xmltv>, ...
starting_number = 1
```

Migración: nada que migrar (sección nueva). Si no existe, el menú muestra el
wizard vacío.

## Implementation Plan

1. `models.py` + `filter_engine.py` con tests unitarios (semántica xTeVe).
2. `epg_matcher.py` con tests (normalización, fallback, sugerencias).
3. `pipeline.py` + `playlist_builder.py` con tests (fixtures M3U/XMLTV reales
   reducidos, incluido caso Pluto TV sin tvg-name).
4. `playlist_server.py` con tests (start/stop, endpoints, contenido cacheado).
5. `list_manager_dialog.py`: wizard 3 pasos + tabla de filtros + editor EPG.
6. Integración en `main_window.py` (submenú "Gestor de listas") y `main.py`
   (load/save `[listmanager.*]`).
7. Smoke test manual: servir lista real de España filtrada y cargarla en VLC.

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
- [ ] `http://127.0.0.1:<port>/playlist.m3u` y `/epg.xml` responden con el
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
