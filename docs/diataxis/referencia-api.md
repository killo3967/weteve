# Referencia de la API

Un único puerto (`control_port`, 34400 por defecto) sirve todo. El token, si está
definido, se manda como `Authorization: Bearer <token>`; la playlist y los logos
quedan abiertos a propósito porque los reproductores no mandan cabeceras.

## Playlist y guía (lo que consume el reproductor)

| Ruta | Contenido |
|---|---|
| `GET /m3u/weteve_<perfil>.m3u` | lista M3U del perfil, numerada (`tvg-chno`) |
| `GET /xmltv/weteve_<perfil>.xml` | XMLTV con solo los canales servidos |
| `GET /logos/<fichero>` | logo local de `assets/logos` |

- El `<perfil>` es el id numérico (`0`, `1`…). Cualquier otro valor da 404.
- La cabecera del M3U lleva `url-tvg="…/xmltv/weteve_<perfil>.xml"`: es lo que
  permite a las apps descargar la guía solas.
- No hay otros formatos ni alias: el anterior `/weteve/{id}/playlist.m3u` se
  retiró.

## API de control

| Ruta | Para qué |
|---|---|
| `GET /health` | vivo / 401 si hace falta token |
| `GET /status` | estado agregado (perfiles, URLs, `revision`) |
| `POST /shutdown` | parada elegante |
| `POST /targets/{id}/reload` | reejecutar el pipeline de un perfil |

## REST v1 (`/api/v1`, prefijo fijo)

Límite de página: 1000. Todas las lecturas salen del snapshot; solo `run` y
`refresh-epg` ejecutan.

| Ruta | Qué hace |
|---|---|
| `GET /health`, `GET /status` | estado |
| `GET /targets`, `GET /targets/{id}` | perfiles |
| `GET /targets/{id}/status` | estado de ejecución |
| `GET /targets/{id}/channels` | canales servidos (`?limit&offset&unmatched_only`) |
| `GET /targets/{id}/mapping-candidates` | filas de la tabla (`?include_served`) |
| `GET /targets/{id}/epg-channels` | canales de la guía |
| `GET /targets/{id}/catalog` | catálogo con dial, código y «en tu lista» |
| `GET /targets/{id}/groups`, `/group-fields` | valores de agrupación |
| `GET /sources` | cartera (`?kind=m3u\|epg`) |
| `POST /targets/{id}/preview` | vista previa de unas reglas |
| `POST /targets/{id}/run` | ejecutar el pipeline |
| `POST /targets/{id}/refresh-epg` | volver a leer las guías (descarga) |
| `PUT /targets/{id}/settings` | nombre, puerto, bind, prioridad, dummy, guía ligera |
| `PUT /targets/{id}/rules` | filtros por campos |
| `PUT /targets/{id}/mappings` | mapeos EPG en lote |
| `PUT /targets/{id}/channels` | fichas: dial, nombre mostrado y logo |
| `PUT /targets/{id}/sources` | selección de fuentes del perfil |
| `PUT /targets/{id}/disabled` | canales apagados a mano |

### `PUT /targets/{id}/channels`

```json
{"channels": [
  {"identity": "COD1", "dial": 54, "display": "Canal Uno", "logo": "COD1.png"}
]}
```

- `identity` es la clave de la ficha (`COD1`, `url-<hash>`, `name-<base>`).
- Campo ausente = no se toca. `null`/`""` = vuelve al automático.
- `logo` acepta **nombre** de `assets/logos`, **ruta** a una imagen (se copia) o
  **URL http(s)** (se descarga y se guarda).
- Todo o nada: un dial repetido o un logo imposible devuelve error y no guarda.

## MCP (`POST /mcp`, JSON-RPC 2.0)

`initialize` devuelve `Mcp-Session-Id`, que hay que repetir en cada `tools/call`.
Cabecera `Accept: application/json, text/event-stream`.

24 herramientas:

| Grupo | Herramientas |
|---|---|
| Estado | `get_status`, `list_targets`, `get_target` |
| Perfiles | `create_target`, `update_target`, `delete_target`, `start_target`, `stop_target`, `reload_target`, `run_pipeline` |
| Lectura de canales | `list_tv_channels`, `list_epg_channels`, `suggest_epg_matches` |
| Cartera | `add_source`, `list_sources`, `update_source`, `remove_source`, `set_target_sources` |
| Filtros | `create_filter`, `delete_filter` |
| Mapeo y fichas | `modify_mapping`, `modify_mappings`, `modify_channels` |
| Demonio | `shutdown` |

Ejemplo mínimo:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call",
 "params":{"name":"modify_channels","arguments":{"target_id":"0",
   "channels":[{"identity":"COD1","display":"Canal Uno"}]}}}
```

## Campos de `/status` por perfil

| Campo | Significado |
|---|---|
| `running` | si está sirviendo |
| `channel_count` | canales servidos tras filtros y dedupe |
| `playlist_url`, `epg_url` | URLs públicas (`/m3u/…`, `/xmltv/…`) |
| `revision` | sube con cada guardado o recarga; la GUI lo usa para repintar |
| `error` | último fallo del pipeline, si lo hubo |
