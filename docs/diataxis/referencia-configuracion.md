# Referencia de `config.ini`

Tres tipos de sección. El fichero lo escribe **solo el demonio** (serializador
único `config_store`); la GUI únicamente escribe `[DAEMON]`, que el demonio lee
al arrancar. `config.ini` no se versiona (sí `config.ini.example`).

## `[DAEMON]` — cómo escucha el demonio

| Clave | Default | Descripción |
|---|---|---|
| `control_host` | `127.0.0.1` | **único** bind: control, REST, MCP y playlists |
| `control_port` | `34400` | **único** puerto de todo |
| `mcp_port` | *(ignorado)* | legado: se registra un aviso si difiere de `control_port` |
| `mcp_token` | *(vacío)* | si se define, `Authorization: Bearer` obligatorio |
| `autostart` | `0` | la GUI arranca el demonio al abrir si no responde |
| `stop_on_exit` | `0` | la GUI lo para al cerrar **solo si lo arrancó ella** |

Con `control_host` distinto de loopback y `mcp_token` vacío el demonio **no
arranca** (falla cerrado). Ese bind afecta a **todo**: si publicas la playlist en
la red, la API y el MCP quedan en la misma superficie (token obligatorio).

## `[listmanager.target.N]` — un objetivo de salida

| Clave | Formato | Notas |
|---|---|---|
| `name` | texto | nombre del resultado |
| `custom_m3u` | ruta/URL | fuente única heredada; se conserva aunque se use la cartera |
| `custom_epg` | ruta/URL | ídem para la guía |
| `epg_source` | entero | legado; `-1` = sin índice de la app anfitriona |
| `port` | entero | **ignorado** (legado): la playlist va por el puerto del demonio |
| `bind` | texto | **ignorado** (legado): manda `[DAEMON] control_host` |
| `starting_number` | entero | primer `tvg-chno` (default 1) |
| `rules` | ver abajo | filtros, separados por `\|` |
| `epg_overrides` | `url::channel-id` | mapeos manuales, separados por `;;` |
| `disabled_channels` | urls | canales apagados a mano, separados por `\|\|` |
| `m3u_sources` | ids | filas de cartera M3U del target, separadas por coma |
| `epg_sources` | ids | filas de cartera EPG del target |

### Reglas (`rules`)

Sintaxis xTeVe: `campo:patrón:case[:operador]`, unidas por `|`.

- `campo`: `group-title` | `custom-filter` | `tvg-group` | `extgrp` | `source`.
- `patrón`: texto a buscar; `{texto}` incluye; `!{texto}` excluye (la exclusión
  gana siempre, antes de evaluar operadores).
- `case`: `0` insensible, `1` respeta mayúsculas.
- `operador`: `or` (default, se omite) o `and`. Las reglas `and` son
  obligatorias; entre las `or` basta una.

```ini
rules = group-title:SPAIN:0:and|custom-filter:!{HBO}:0:and|group-title:Cine:0:or
```

### Mapeos EPG (`epg_overrides`)

`url-del-canal::channel-id` separados por `;;`. El `channel-id` es el del XMLTV;
`Dummy 1h` se guarda como id sintético (`weteve-dummy-…`) en esta misma clave.
Gana siempre sobre el emparejamiento automático (`tvg-id` y luego nombre
normalizado), que **no se persiste**: se recalcula en cada ejecución.

```
epg_overrides = http://host/canal/1::La 1 HD;;http://host/canal/2::La 2
```

### Desactivados (`disabled_channels`)

URLs de canal separadas por `||`. Se excluyen antes de numerar; siguen visibles
en la tabla de mapeo para poder reactivarlos.

## `[source.<id>]` — cartera global de fuentes

| Clave | Valores | Notas |
|---|---|---|
| `name` | texto | etiqueta para el filtro `source` |
| `kind` | `m3u` \| `epg` | |
| `source_type` | `file` \| `url` \| `movistar` | se infiere de `location` si falta |
| `location` | ruta, URL http(s) o `movistar://epg` | una URL sin host (`http://`) se rechaza |
| `refresh_min` | entero ≥ 0 | minutos entre recargas; `0` = estática |
| `enabled` | `1` \| `0` | fila disponible para los targets |

Cada target elige filas con `m3u_sources` / `epg_sources` (ids separados por
coma). Sin ninguna fila seleccionada, el target usa `custom_m3u`/`custom_epg`.

### Guía de Movistar+ (`movistar`)

`location = "movistar://epg"` (o `movistar://epg?span=7`, de 1 a 7 días; por
defecto 3, que son 4 días de parrilla). No es XMLTV: son dos endpoints JSON de
la API OTT de Movistar+, y además de los programas publica el **dial** y el
**logo** de cada canal.

Eso cambia la lista servida: `tvg-chno` pasa a ser el dial real (1, 2, 3, 4, 7…,
con sus huecos) y `tvg-logo`, el logo vigente del proveedor. Los canales sin
dial (los que no están en el dial de Movistar+) se numeran a continuación del
último dial. Cuando dos guías anuncian el mismo canal, gana la que publica
dial y logo, aunque la otra lo anuncie con un nombre exacto.

Un mapeo manual (`epg_overrides`) sigue mandando: si apunta a un id de otra
guía, ese canal deja de recibir dial y logo de Movistar+.

## Migración automática

Una config antigua de fuente única (`custom_m3u`/`custom_epg` sin
`m3u_sources`/`epg_sources`) se migra sola al arrancar el demonio: crea las filas
`[source.*]` y enlaza el target. Es idempotente y solo escribe si hay algo que
migrar; la GUI ya no migra nada.
