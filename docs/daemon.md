# Demonio weteve

El demonio es el servicio permanente de weteve: posee los targets (pipeline +
servidor HTTP de playlist por target), expone una API de control para la UI Qt
y un endpoint **MCP** completo para que agentes (Claude, etc.) lo gobiernen.

## Arquitectura

```
config.ini  ←── TargetStore ──→ TargetSupervisor ──→ PlaylistServer por target
                   ↑                  ↑                     (playlist.m3u + epg.xml)
                   │                  │
        UI Qt (DaemonPanel)   ┌──────┴───────┐
                              │ ControlApi   │  puerto [DAEMON] control_port (34400)
        CLI (daemon.py)  ───→ │ (health,     │  → UI y CLI
                              │  status,     │
                              │  reload,     │
                              │  shutdown)   │
                              ├──────────────┤
                              │ MCP Streamable│ puerto [DAEMON] mcp_port (34401)
                              │ HTTP /mcp     │  → agentes (control total)
                              └──────────────┘
```

- **Qt es UI-only**: el diálogo guarda la config en `config.ini` y puede
  **arrancar/parar/reiniciar el demonio** (botones del panel «Demonio»). Arrancar
  desde la GUI abre una **consola propia con los logs en vivo**; el demonio también
  escribe `logs/weteve-daemon.log` (rotación 1 MB × 3). El demonio puede vivir sin
  UI abierta.

## Quién tiene la config: flujo de Guardar → demonio

`config.ini` es el único estado compartido, pero **ninguno de los dos procesos es
su dueño**: hay que reconciliarlo de forma explícita.

1. **Guardar** (diálogo) escribe `config.ini` y, si el demonio responde al
   `/health`, hace `POST /targets/{id}/reload`. El `result_label` dice si el
   demonio fue avisado o si no respondió (en ese caso el servicio sigue con la
   config anterior hasta que lo arranques o pulses «Aplicar»).
2. **El reload relee el fichero**: `TargetStore.reload(target_id)` vuelve a parsear
   `config.ini` y devuelve la definición fresca, que `ControlApiServer` pasa a
   `supervisor.reload_target(...)` como `target`. Sin store (o para targets nunca
   arrancados) se conserva el comportamiento antiguo: recargar lo que el supervisor
   ya tenía en memoria.
3. **MCP** usa la misma definición fresca en `start_target`, `reload_target` y
   `run_pipeline`; lo que otro proceso haya escrito en el fichero se ve sin
   reiniciar el demonio.

No hay file watcher: un reload provocado es más predecible que reaccionar a cada
escritura (y evita recargar a media escritura).

## Logs

- **Consola**: al arrancar en primer plano (`daemon.py run`) o desde la GUI
  (consola propia del proceso).
- **Fichero**: `logs/weteve-daemon.log` siempre, rota a 1 MB guardando 3
  backups. Nivel INFO, mismo formato que consola.
- **El MCP es la superficie de control total** (CRUD incluido); la API de
  control HTTP es lo mínimo que la UI necesita.

## Configuración (`config.ini`, sección `[DAEMON]`)

| Clave | Default | Descripción |
|---|---|---|
| `control_host` | `127.0.0.1` | Bind de ambas APIs |
| `control_port` | `34400` | API de control (UI/CLI) |
| `mcp_port` | `34401` | Endpoint MCP Streamable HTTP |
| `mcp_token` | *(vacío)* | Si se define: `Authorization: Bearer` obligatorio en MCP y API de control |

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

## Herramientas MCP (17)

Transporte: JSON-RPC 2.0 Streamable HTTP (`POST /mcp`), sesión vía cabecera
`Mcp-Session-Id` asignada en `initialize`. Sin dependencias del SDK oficial
(“swappable” a futuro).

| Grupo | Herramientas |
|---|---|
| Estado | `get_status`, `shutdown` |
| Lectura | `list_targets`, `get_target` |
| CRUD targets | `create_target`, `update_target`, `delete_target` |
| Ciclo de vida | `start_target`, `stop_target`, `reload_target` |
| Pipeline | `run_pipeline`, `preview_channels` |
| Cartera de fuentes | `add_source`, `list_sources`, `update_source`, `remove_source`, `set_target_sources` |

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
