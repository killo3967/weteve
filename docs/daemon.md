# Demonio weteve

> **Vocabulario:** lo que el código llama *target* es un **Perfil** en la
> interfaz y en esta documentación: una lista + filtros + guía + numeración.

El demonio es el servicio permanente de weteve: posee los targets (pipeline +
servidor HTTP de playlist por target), expone una API de control para la UI Qt
y un endpoint **MCP** completo para que agentes (Claude, etc.) lo gobiernen.

## Arquitectura

```
config.ini  ←── config_store ──→ TargetStore ──→ TargetService ──→ TargetSupervisor
 (serializador único)                                  │                 │
                                                       │                 └→ PlaylistServer por target
                                                       │                    (/m3u/weteve_<perfil>.m3u + /xmltv/weteve_<perfil>.xml)
        UI Qt (frontend) ── REST /api/v1 ─┐            │
        CLI (daemon.py)  ── control ──────┤            │
        Agentes          ── MCP —─────────┤            │
                                          ▼            ▼
                                    ┌──────────────────────────┐
                                    │ McpHttpTransport          │ puerto [DAEMON] mcp_port (34401)
                                    │  /api/v1  (REST v1)       │  REST → UI/web
                                    │  /mcp     (JSON-RPC)      │  MCP → agentes
                                    ├──────────────────────────┤
                                    │ ControlApiServer          │ puerto [DAEMON] control_port (34400)
                                    │  health/status/reload/    │  → UI y CLI
                                    │  shutdown                 │
                                    └──────────────────────────┘
```

La lógica de dominio vive en **`TargetService`** (`src/application/services/daemon/service.py`);
REST y MCP son adaptadores finos sobre ella: no hay reglas de negocio duplicadas.
Las lecturas salen del snapshot del supervisor y **no reejecutan** el pipeline.

- **Qt es un frontend**: no ejecuta el pipeline ni sirve playlists. Pinta lo que
  le da `GET /api/v1/...` y manda órdenes con `PUT/POST`. Puede
  **arrancar/parar/reiniciar el demonio** (panel «Demonio») y, si las casillas
  `autostart`/`stop_on_exit` están marcadas, lo hace al abrir/cerrar; solo para el
  demonio que arrancó ella. Arrancar desde la GUI lo lanza **oculto, sin ventana
  de cmd** (`pythonw.exe` + `DETACHED_PROCESS`); los logs van a
  `logs/weteve-daemon.log` (rotación 1 MB × 3). `python.exe` no sirve aunque se
  pase `DETACHED_PROCESS`: el redirector del venv recrea el proceso y Windows le
  asigna una consola nueva, que se ve como una ventana de cmd. El demonio puede
  vivir sin UI abierta.

## Quién escribe la config: flujo de Guardar

El **demonio es el único escritor** de `config.ini` (`TargetStore` y la cartera),
con un único serializador (`src/infrastructure/client/config_store.py`). La UI no
toca el fichero (salvo `[DAEMON]`, que el demonio solo lee al arrancar):

1. **Guardar** (diálogo) manda un lote por REST con `reload: false`:
   `PUT /sources`, `PUT /targets/{id}/sources`, `/settings`, `/rules`,
   `/mappings`, `/disabled`.
2. Si una escritura falla, el diálogo avisa y **no escribe nada más**: no hay
   guardados a medias. Sin demonio, Guardar no hace nada y lo dice.
3. El lote termina con **una sola** ejecución: `POST /targets/{id}/run`. El
   demonio relee la definición del fichero (`TargetStore.reload`), ejecuta el
   pipeline y actualiza el contenido servido.
4. **MCP** opera sobre el mismo `TargetService`; cualquier cambio hecho por un
   agente se ve en la UI en cuanto se refresca (la UI no tiene copia del motor).

## Logs

- **Consola**: solo si arrancas `daemon.py run` en primer plano. Desde la GUI el
  proceso va oculto (`pythonw.exe`) y no abre ninguna ventana de cmd.
- **Fichero**: `logs/weteve-daemon.log` siempre, rota a 1 MB guardando 3
  backups. Nivel INFO, mismo formato que consola:
  `fecha [nivel] logger: mensaje`, con el nivel corto y en minúsculas
  (`[info]`, `[warn]`, `[error]`, `[debug]`, `[critical]`).
- **Log de comunicaciones**: los tres servidores HTTP (playlist/EPG, API de
  control y MCP) escriben una línea INFO por petición con el cliente, la
  petición y el código de estado, y una WARNING por rechazo (token inválido,
  ruta desconocida, JSON inválido, GET sin SSE) o por conexión interrumpida
  (RST de loopback). En MCP se registra además el método JSON-RPC
  (`initialize`, `tools/list`, `tools/call`…). Implementado en
  `src/infrastructure/web/http_common.py` (`AccessLogMixin`, `NoReuseServer`).
- **Fallos del cliente**: `DaemonClient` registra a WARNING cuando el demonio
  no responde o devuelve error HTTP; la UI ya no muestra «no responde» sin
  dejar rastro en `logs/weteve.log`.
- **El MCP es la superficie de control total** (CRUD incluido); la API de
  control HTTP es lo mínimo que la UI necesita.

## Configuración (`config.ini`, sección `[DAEMON]`)

| Clave | Default | Descripción |
|---|---|---|
| `control_host` | `127.0.0.1` | Bind de ambas APIs |
| `control_port` | `34400` | **único** puerto: control, REST (`/api/v1`), MCP (`/mcp`) y playlists (`/m3u/weteve_<perfil>.m3u`, `/xmltv/weteve_<perfil>.xml`, `/logos/<f>`) |
| `mcp_port` | *(ignorado)* | legado; el demonio avisa si difiere de `control_port` |
| `mcp_token` | *(vacío)* | Si se define: `Authorization: Bearer` obligatorio en MCP, API de control y REST |
| `autostart` | `0` | La interfaz arranca el demonio al abrirse si no responde |
| `stop_on_exit` | `0` | La interfaz para el demonio al cerrarse **solo si lo arrancó ella** |

Con `control_host` distinto de loopback y `mcp_token` vacío el demonio **no
arranca**: la API no distingue usuarios y quedaría expuesta a la red.

## Uso

```bat
:: Arrancar en primer plano (lo que ejecuta la tarea programada)
.venv\Scripts\python.exe daemon.py run

:: Consultar estado (exit 0 = vivo)
.venv\Scripts\python.exe daemon.py status

:: Apagar
.venv\Scripts\python.exe daemon.py stop

:: Registrar arranque automático al iniciar sesión (Task Scheduler)
.venv\Scripts\python.exe daemon.py install-task
```

### Host de control

Se elige en un desplegable de la pestaña Configuración: `127.0.0.1` (solo este
equipo), la IP con la que el equipo sale a la red (la que necesita el
reproductor), el resto de interfaces y `0.0.0.0` (todos los interfaces).

- Fuera de loopback **hace falta `mcp_token`**: el guardado no se permite sin
  él (hay un botón «Generar»). El demonio, además, se niega a arrancar sin
  token en ese caso.
- Con `0.0.0.0` se anuncia la IP de la red en `playlist_url` y en los logos:
  `0.0.0.0` no es una dirección de destino.
- Cambiar el host o el puerto **no** cambia el proceso en marcha: al guardar,
  el panel reinicia el demonio y verifica que responde; si no, muestra el
  motivo (la última línea de su log).

### Instancia única

Solo puede haber **un** demonio por `daemon.toml`. El arranque toma un candado
del sistema operativo **antes** de leer la configuración:

- `weteve.lock` (junto a `daemon.toml`): el bloqueo. No lleva contenido y lo
  libera el sistema si el proceso muere, así que no hay que limpiar nada.
- `weteve.pid`: `pid host:puerto` en texto plano, para saber quién corre y
  dónde. Va aparte porque en Windows un bloqueo por rango impide leer esos
  bytes.

Si el candado ya está tomado, el segundo proceso **no arranca**: consulta el
`/health` de la dirección anotada y sale con `ya está en marcha en host:puerto`
(exit 0). Si el candado está tomado pero nadie responde (demonio colgado o
arrancando), sale con exit 2 y un aviso: nunca se abre un segundo.

Esto cubre el caso que el puerto no cubre: dos demonios con distinto
`control_port` escribirían el mismo `daemon.toml` y se pisarían los guardados.
La GUI usa el mismo candado para no lanzar otro cuando el que corre está en otro
puerto del configurado.

## Herramientas MCP (24)

Transporte: JSON-RPC 2.0 Streamable HTTP (`POST /mcp`), sesión vía cabecera
`Mcp-Session-Id` asignada en `initialize`. Sin dependencias del SDK oficial
(“swappable” a futuro).

| Grupo | Herramientas |
|---|---|
| Estado | `get_status`, `shutdown` |
| Lectura | `list_targets`, `get_target` |
| CRUD targets | `create_target`, `update_target`, `delete_target` |
| Ciclo de vida | `start_target`, `stop_target`, `reload_target` |
| Filtros y mapeo | `create_filter`, `delete_filter`, `modify_mapping`, `modify_mappings` |
| Pipeline | `run_pipeline`, `list_tv_channels`, `list_epg_channels`, `suggest_epg_matches` |
| Cartera de fuentes | `add_source`, `list_sources`, `update_source`, `remove_source`, `set_target_sources` |

## API REST v1

Mismo puerto que MCP (`[DAEMON] mcp_port`), prefijo `/api/v1`, misma
autenticación. Es el contrato de los frontends (Qt hoy, web mañana).

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/health`, `/status`, `/targets`, `/targets/{id}`, `/targets/{id}/status` | estado |
| GET | `/targets/{id}/channels?limit&offset&unmatched_only` | canales servidos |
| GET | `/targets/{id}/mapping-candidates` | canales que necesitan atención (sin guía / desactivados) |
| GET | `/targets/{id}/epg-channels`, `/groups`, `/group-fields` | catálogo y combos |
| POST | `/targets/{id}/preview` | vista previa de filtros (no persiste) |
| POST | `/targets/{id}/run`, `/targets/{id}/service` | ejecutar pipeline / arrancar-parar el servicio |
| PUT | `/targets/{id}/settings`, `/rules`, `/mappings`, `/disabled` | escrituras en lote |

Las escrituras aceptan `"reload": false` para agrupar varios cambios y ejecutar el
pipeline una sola vez al final con `POST .../run`. Códigos: 200, 400 (cuerpo o
cambio inválido), 404, 405. CORS solo para orígenes de loopback.

Las reglas de filtro viajan en sintaxis xTeVe textual (`campo:{patrón}:case`,
`!{...}` para exclusiones), igual que en `config.ini`.

## Cartera de fuentes y multi-origen

`config.ini` guarda una cartera global de listas en secciones
`[source.<id>]` (`name`, `kind` = `m3u|epg`, `source_type` = `file|url`,
`location`, `refresh_min`, `enabled`). Cada target **selecciona** filas con
las claves `m3u_sources` / `epg_sources` (ids separados por comas) en su
sección `listmanager.target.N`; la playlist servida es la **unión** de las
listas M3U activas y la guía la unión de las EPG activas. Los canales
replicados entre listas NO se deduplican: se muestran como `<Lista>.<Canal>`
(prefijo solo de presentación; filtros y casado EPG ven el nombre limpio).
El campo de filtro `source` (etiqueta "Lista" en la GUI) filtra por nombre de
lista de origen.

Cada fuente tiene un refresco periódico (`refresh_min`, 0 = estática). El
demonio reejecuta el pipeline de cada target cada
`min(refrescos de sus fuentes activas)`, con suelo de 5 minutos; targets sin
fuentes con refresco no se reprograman. Los overrides EPG manuales se
conservan entre ciclos; el resto del mapeo se recalcula en cada recarga.

### Ejemplo de sesión MCP

```json
POST http://127.0.0.1:34401/mcp
{"jsonrpc":"2.0","id":1,"method":"initialize",
 "params":{"protocolVersion":"2025-03-26","capabilities":{},
           "clientInfo":{"name":"agente","version":"0"}}}
→ 200, cabecera Mcp-Session-Id: <sid>

POST /mcp  (con Mcp-Session-Id)
{"jsonrpc":"2.0","method":"notifications/initialized"}
→ 202

POST /mcp
{"jsonrpc":"2.0","id":2,"method":"tools/call",
 "params":{"name":"create_target","arguments":{
   "target_id":"1","name":"Deportes","m3u":"http://…/lista.m3u",
   "epg":"http://…/guia.xml","port":8081,
   "rules":["group-title:{Deportes}:0"]}}}
```

## Módulos (`src/application/services/daemon/`)

| Módulo | Responsabilidad |
|---|---|
| `config.py` | `DaemonConfig`, carga/guarda `[DAEMON]` preservando targets |
| `target_store.py` | CRUD persistente de `listmanager.target.N` (formato compartido con la UI) |
| `supervisor.py` | Único dueño de targets: pipeline + PlaylistServer por target, thread-safe |
| `control_api.py` | API JSON mínima para la UI (`/health`, `/status`, `/targets/{id}/reload`, `/shutdown`) |
| `mcp_server.py` | Núcleo del protocolo MCP (initialize, sesiones, tools, JSON-RPC 2.0) |
| `mcp_tools.py` | Registro de las 12 herramientas sobre supervisor + store |
| `mcp_transport.py` | Servidor HTTP Streamable (POST/DELETE /mcp, Bearer opcional) |

`src/infrastructure/client/daemon_client.py`: cliente urllib usado por la UI
y la CLI (ping/status/reload/shutdown, con reintentos ante RST de loopback).

## Flakes conocidos (Windows)

Conexiones loopback inmediatas tras `bind` pueden recibir
`ConnectionResetError [WinError 10054]` de forma esporádica (Defender/backlog).
No es un defecto del código: los clientes (`DaemonClient`) y los tests reintentan.
