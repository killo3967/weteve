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

- **Qt es UI-only**: el diálogo guarda la config vía `TargetStore` y pide
  `reload` por la API de control; el demonio puede vivir sin UI abierta.
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

## Herramientas MCP (12)

Transporte: JSON-RPC 2.0 Streamable HTTP (`POST /mcp`), sesión vía cabecera
`Mcp-Session-Id` asignada en `initialize`. Sin dependencias del SDK oficial
(“swappable” a futuro).

| Grupo | Herramientas |
|---|---|
| Estado | `get_status`, `shutdown` |
| Lectura | `list_targets`, `get_target` |
| CRUD | `create_target`, `update_target`, `delete_target` |
| Ciclo de vida | `start_target`, `stop_target`, `reload_target` |
| Pipeline | `run_pipeline`, `preview_channels` |

Las reglas de filtro viajan en sintaxis xTeVe textual (`campo:{patrón}:case`,
`!{...}` para exclusiones), igual que en `config.ini`.

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
