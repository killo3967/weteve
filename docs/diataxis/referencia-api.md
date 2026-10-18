# Referencia de la API

**Todo se sirve en un solo puerto**, `[DAEMON] control_port` (34400). Un único
listener con estas rutas:

| Ruta | Para quién | Token |
|---|---|---|
| `/mcp` | agentes (JSON-RPC) | sí |
| `/api/v1/...` | frontends (GUI Qt, futura web) | sí |
| `/health`, `/status`, `/shutdown`, `/targets/{id}/reload` | API de control (GUI/CLI) | sí |
| `/weteve/{id}/playlist.m3u` y `/weteve/{id}/epg.xml` | reproductores IPTV | **no** |
| `/weteve/playlist.m3u`, `/weteve/epg.xml` | forma corta del target 0 | no |
| `/playlist.m3u`, `/epg.xml` | alias heredados del target 0 | no |

Token opcional por `[DAEMON] mcp_token` (`Authorization: Bearer <token>`); es
**obligatorio** si `control_host` no es loopback. La playlist queda sin token a
propósito: los reproductores IPTV no mandan `Authorization`.

REST y MCP son adaptadores finos sobre `TargetService` (ver
[`explicacion-arquitectura.md`](explicacion-arquitectura.md)).

---

## REST v1

Prefijo `/api/v1`. Respuestas JSON. Errores: `400` (cuerpo o cambio inválido),
`404` (ruta o target), `405` (método). `limit` se capa a 1000. `compatible` con
CORS solo para orígenes de loopback.

### Lecturas

| Ruta | Devuelve |
|---|---|
| `GET /health` | `{"ok": true, "api": "v1"}` |
| `GET /status` | estado de todos los targets (`running`, `port`, `channel_count`, `playlist_url`, `epg_url`, `error`) |
| `GET /targets` | definiciones serializadas (reglas, mapeos, puerto…) |
| `GET /targets/{id}` | `{"target": {...}}` |
| `GET /targets/{id}/status` | estado de ese target |
| `GET /targets/{id}/channels?limit&offset&unmatched_only` | canales servidos |
| `GET /targets/{id}/mapping-candidates?limit&offset` | canales que necesitan atención (sin guía o desactivados) |
| `GET /targets/{id}/epg-channels?limit&offset` | catálogo XMLTV (`id`, `display_name`) |
| `GET /targets/{id}/groups` | grupos de la fuente (combos de reglas) |
| `GET /targets/{id}/group-fields` | valores por campo de agrupación |
| `GET /sources` | cartera completa |

### Escrituras

Todas aceptan `"reload": false` para agrupar cambios y ejecutar el pipeline una
sola vez al final con `POST /run`. Sin ese campo, cada escritura recarga.

| Ruta | Cuerpo | Efecto |
|---|---|---|
| `PUT /sources` | `{"sources": [{id?, name, kind, location, source_type?, refresh_min?, enabled?}]}` | reemplaza la cartera; **rechaza todo el lote** si una entrada es inválida |
| `PUT /targets/{id}/sources` | `{"m3u_sources": [id], "epg_sources": [id]}` | selección del target; ids inexistentes → 400 |
| `PUT /targets/{id}/settings` | `{"name"?, "port"?, "bind"?}` | nombre, puerto (1-65535), bind (`127.0.0.1` o `0.0.0.0`) |
| `PUT /targets/{id}/rules` | `{"rules": ["group-title:SPAIN:0:and", ...]}` | reemplaza las reglas |
| `PUT /targets/{id}/mappings` | `{"mappings": [{"channel_url", "epg_id"\|null}]}` | mezcla por URL; `null` borra; devuelve `applied`/`errors`/`partial` |
| `PUT /targets/{id}/disabled` | `{"disabled_channels": [url, ...]}` | reemplaza los desactivados |
| `POST /targets/{id}/preview` | `{"rules": [...]}` | vista previa de filtros, no persiste |
| `POST /targets/{id}/run` | — | ejecuta el pipeline con la definición en disco; devuelve métricas |
| `POST /targets/{id}/service` | `{"action": "start"\|"stop"}` | arranca/para el servidor de playlist del target |

---

## MCP (`POST /mcp`)

JSON-RPC 2.0 Streamable HTTP. Flujo: `initialize` (devuelve
`Mcp-Session-Id`) → notificaciones con esa cabecera → `tools/list` /
`tools/call`. `GET /mcp` no soporta SSE (405); `DELETE /mcp` cierra la sesión.

23 herramientas, agrupadas:

| Grupo | Herramientas |
|---|---|
| Estado | `get_status()`, `shutdown()` |
| Targets | `list_targets()`, `get_target(target_id)`, `create_target(target_id, name, m3u, port, epg?, bind?, rules?, epg_overrides?, starting_number?)`, `update_target(target_id, …)`, `delete_target(target_id)` |
| Ciclo de vida | `start_target(target_id)`, `stop_target(target_id)`, `reload_target(target_id)`, `run_pipeline(target_id)` |
| Canales/EPG | `list_tv_channels(target_id, limit?, offset?, unmatched_only?)`, `list_epg_channels(target_id, limit?, offset?)`, `suggest_epg_matches(target_id, channel_name, limit?)` |
| Filtros | `create_filter(target_id, field, pattern, exclude?, case_sensitive?, operator?)`, `delete_filter(target_id, field, pattern)` |
| Mapeo | `modify_mapping(target_id, channel_url, epg_id?)`, `modify_mappings(target_id, mappings)` |
| Cartera | `add_source(name, kind, location, refresh_min?)`, `list_sources(kind?)`, `update_source(id, name?, location?, refresh_min?)`, `remove_source(id)`, `set_target_sources(target_id, m3u_ids?, epg_ids?)` |

Reglas y mapeos usan la sintaxis de `config.ini` (ver
[`referencia-configuracion.md`](referencia-configuracion.md)).

---

## API de control

Lo mínimo para la GUI y la CLI:

| Ruta | Efecto |
|---|---|
| `GET /health` | `{"ok": true}` si el demonio vive |
| `GET /status` | estado agregado (el mismo que `GET /api/v1/status`) |
| `POST /targets/{id}/reload` | relee `config.ini` y reejecuta el pipeline |
| `POST /shutdown` | parada elegante |

---

## Playlist y EPG (mismo puerto)

| Ruta | Contenido |
|---|---|
| `GET /weteve/{id}/playlist.m3u` | M3U filtrada y numerada (`tvg-chno`) |
| `GET /weteve/{id}/epg.xml` | XMLTV solo con los canales servidos |
| `GET /weteve/playlist.m3u`, `GET /playlist.m3u` | alias del target 0 |

El supervisor guarda el contenido en memoria; no hay un servidor por target.

El `tvg-id` del M3U sale **vacío a propósito**: el enlace canal↔guía es por
nombre, como en las listas de proveedor. Cualquier otra ruta devuelve 404.
