# Arquitectura y decisiones

**Respuesta corta**: hay un solo motor (el demonio) con un solo escritor de
configuración, y N frontends tontos. La GUI de Qt no ejecuta pipeline ni sirve
playlists: pregunta y ordena por HTTP.

## Piezas

```
GUI Qt ──REST──┐
CLI ───control─┤
agentes ──MCP──┤
               ▼
        McpHttpTransport  (control + /api/v1 + /mcp + /weteve/…, UN puerto)
               │
        TargetService  ← la lógica de dominio, sin HTTP ni Qt
          │         │
   TargetStore   TargetSupervisor ──► Pipeline ──► contenido en memoria
   (config_store)      │                           (M3U + XMLTV por target)
                       └── snapshot en memoria del último resultado
```

| Capa | Responsabilidad |
|---|---|
| `src/domain` | entidades (`Channel`, `EPGData`) y puertos; sin dependencias |
| `src/application/services/list_manager` | pipeline: parseo M3U → filtros → EPG → M3U/XMLTV de salida |
| `src/application/services/daemon` | `TargetService`, `TargetSupervisor`, `TargetStore`, REST/MCP |
| `src/infrastructure` | adaptadores: ficheros, HTTP, Qt, clientes |

REST y MCP son **adaptadores finos**: traducen y delegan en `TargetService`. Si
una ruta y una herramienta hacen lo mismo, la lógica está una sola vez.

## Decisiones y por qué

### Un único escritor de `config.ini`

Todo pasa por `config_store` (targets, cartera y `[DAEMON]`). Antes escribían
cuatro módulos con serializadores distintos, y eso provocó pérdidas reales: la
GUI guardaba desde su estado en memoria y borraba mapeos hechos por MCP. Hoy
Guardar es un lote REST y el fichero lo escribe el demonio.

### Lecturas desde snapshot; escrituras con recarga diferida

- Leer (`GET …`) nunca ejecuta el pipeline: se sirve el último resultado.
- Escribir acepta `"reload": false`; la GUI manda cartera + fuentes + ajustes +
  reglas + mapeos y ejecuta **una vez** con `POST /run`. Un pipeline con 37.000
  canales tarda 3-8 s; multiplicarlo por cada cambio era lo que congelaba la UI.

### Dos locks en el supervisor

`_lock` protege el estado (targets, resultados, servidores) y es el único que
tocan las lecturas; `_run_lock` serializa la ejecución del pipeline. Con un solo
lock, `GET /status` esperaba detrás de una recarga completa.

### Un solo puerto y un solo bind

Control, REST, MCP y playlists comparten `[DAEMON] control_port`. Ventajas: una
URL base, un chequeo de «puerto ocupado», sin `port`/`bind` por target. Coste: un
único bind, así que publicar la playlist en la LAN expone también la API y el MCP
(de ahí el token obligatorio fuera de loopback). La playlist se sirve **sin**
token porque los reproductores IPTV no mandan `Authorization`; los alias
`/playlist.m3u` y `/epg.xml` se mantienen para no romper URLs ya configuradas.

### La GUI es un frontend

No tiene pipeline, ni servidor, ni escritura de configuración. Sus pestañas se
pintan con `mapping-candidates`, `epg-channels`, `groups` y `preview`. Si el
demonio no responde lo dice y no inventa estado.

### Fallar cerrado en lo peligroso

- `control_host` fuera de loopback sin `mcp_token` → el demonio no arranca.
- `PUT /sources` con una entrada inválida → rechaza el lote entero (no vacía la
  cartera a medias).
- Ids de cartera desconocidos en `set_target_sources` → 400 (antes se perdían en
  silencio).
- Una URL sin host (`http://`) se rechaza: era el caso de la fila `abilio`.

### Detalles de plataforma (Windows)

- Cerrar un socket HTTP recién escrito provoca RST intermitente en loopback
  (~50 % medido): los servidores duermen ~20 ms antes de cerrar. Esto eliminó los
  fallos intermitentes de la suite y los resets a reproductores.
- Una conexión a puerto cerrado tarda ~2 s en esta máquina: los clientes no
  reintentan `ConnectionRefusedError`.

## Flujo completo de un «Guardar»

1. La GUI calcula el lote y llama a `PUT /sources` (cartera visible).
2. `PUT /targets/{id}/sources` (selección), `/settings`, `/rules`, `/mappings`
   (con `epg_id: null` para lo que el usuario quitó) y `/disabled`, todas con
   `"reload": false`.
3. `POST /targets/{id}/run` → el demonio relee la definición de disco, ejecuta el
   pipeline y regenera el contenido servido.
4. La GUI vuelve a pedir `mapping-candidates` y `preview`. Si algo falla antes
   del paso 3, avisa y no se ha escrito nada más.

## Modelo de datos del canal

`Channel` (parseado) → `MappedChannel` (con `matched_epg_id` y `chno`). El
emparejamiento EPG es automático (`tvg-id`, luego nombre normalizado) y **no se
persiste**; lo manual (`epg_overrides`) sí, y gana sobre el automático. Los
separadores de sección (`### TOP ###`) se descartan siempre. Con fuente EPG, un
canal sin guía no se sirve: queda en `excluded_no_epg` y aparece en la tabla de
mapeo para rescatarlo (EPG real o `Dummy 1h`).

## Deuda y límites actuales

| Tema | Estado |
|---|---|
| `mypy` en dos widgets Qt heredados | en `[[tool.mypy.overrides]]` (`ignore_errors`) por `findChildren`/`header` Optional |
| Empaquetado (`pip install -e .`) | no hay `[project]`; imports `src.*` desde la raíz |
| `scripts/healthcheck.py` | fuera de CI (necesita daemon vivo) |
| Tests `network` | dependen de las fuentes URL de tu `config.ini`; `abilio` está roto (`http://`) y el EPG de Doble_M supera el guard de 20 MB del test |
| SPA web | la API está lista para un frontend externo; CORS abierto solo a loopback |
