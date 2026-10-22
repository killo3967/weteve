# Referencia de configuración

Dos ficheros, cada uno con su escritor:

| Fichero | Contenido | Escribe |
|---|---|---|
| `daemon.toml` | cartera de fuentes y perfiles (datos) | el demonio |
| `weteve.ini` | host, puerto, token y arranque | la GUI |

`config.ini` es el formato **viejo**; solo se lee para migrar
(`python daemon.py migrate-config`).

**Capa legada congelada el 2026-10-22**: los módulos que leen el INI
(`config_store.parse`, `persistencia.load_config_ini`, `sources_store.read_catalog`)
no crecen ni se usan en el flujo normal; solo los llama `migrate-config`. No hay
fecha de retirada: se mantienen mientras haya ficheros antiguos que convertir.

Ejemplos listos para copiar: `daemon.toml.example` (datos) y
`weteve.ini.example` (arranque).

## `weteve.ini` — `[DAEMON]`

| Clave | Por defecto | Qué hace |
|---|---|---|
| `control_host` | `127.0.0.1` | dónde enlaza. `0.0.0.0` = todos los interfaces |
| `control_port` | `34400` | **único** puerto: control, REST, MCP, playlists y logos |
| `mcp_port` | = `control_port` | obsoleto; se ignora si difiere |
| `mcp_token` | vacío | vacío = sin autenticación (solo loopback). **Obligatorio** fuera de loopback |
| `autostart` | `0` | si la GUI arranca el demonio al abrir |
| `stop_on_exit` | `0` | si la GUI lo para al cerrar (solo si lo arrancó ella) |

## `daemon.toml`

```toml
version = 2
```

### `[[sources]]` — cartera global

| Clave | Notas |
|---|---|
| `id` | corto y estable |
| `name` | se usa como etiqueta de origen (filtro `source`) |
| `kind` | `m3u` (canales) o `epg` (guía) |
| `source_type` | `file`, `url` o `movistar` (se deduce de `location`) |
| `location` | ruta, URL http(s) o `movistar://epg?span=7` |
| `refresh_min` | minutos entre recargas; `0` = estática |
| `priority` | peso al desempatar guías (mayor gana); solo se escribe si no es 0 |
| `enabled` | si participa en la unión |

### `[[targets]]` — un perfil

| Clave | Notas |
|---|---|
| `id` | numérico (`0`, `1`…): es el `<perfil>` de las URLs |
| `name` | nombre visible |
| `port`, `bind` | herencia del modo antiguo; hoy manda `control_port` |
| `starting_number` | número del primer canal de la cola sin dial |
| `m3u_sources`, `epg_sources` | ids de la cartera que aporta el perfil |
| `rules` | filtros en sintaxis xteve (ver abajo) |
| `disabled` | urls apagadas a mano a nivel de perfil (legado por url) |
| `serve_without_epg` | `true` = servir con Dummy 1h los que no tienen guía |
| `light_epg` | solo se escribe si es `false`: guía sin descripciones (por defecto sí) |
| `quality_priority` | `{ "hd" = 9, "full hd" = 8, … }`; más número, más prioridad |

### `[targets.overrides]` — EPG a mano por url

```toml
[targets.overrides]
"http://proveedor/canal/3849" = "M+ LALIGA TV HD"
```

El valor es un channel-id del XMLTV (o `weteve-dummy-…`). Tiene prioridad sobre
el emparejamiento automático.

### `[targets.channels."<identidad>"]` — la ficha del canal

```toml
[targets.channels."MLIGA"]
dial    = 54
display = "M+ LALIGA"
logo    = "MLIGA.png"
alias   = "M+ LaLigaTVHD"     # nombre interno con el que se vio

[targets.channels."url-0df14a8e7a69"]
logo = "Odisea.png"
```

- Clave: el código de guía (`MLIGA`) o, si no lo hay, `url-<hash>` / `name-<base>`.
- `dial` fuerza el número (1–9999, único entre los servidos).
- `display` es el nombre que ve el reproductor; vacío = el de la guía.
- `logo` es un fichero de `assets/logos` (el demonio lo sirve en `/logos/`).
  Esa carpeta está ignorada por git: los logos elegidos a mano se apuntan en
  `assets/logos-manual.json` para poder recuperarlos tras un clon.
- Solo se escriben los campos que difieren del automático.

### Reglas (`rules`)

```
group-title:TEXTO:0:and          incluye si el grupo contiene TEXTO (AND)
custom-filter:!{TEXTO}:0:and     excluye si el nombre contiene TEXTO
```

Operador final `and`/`or`; `{texto}` incluye además, `!{texto}` excluye. La
comparación es subcadena, sin expresiones regulares.

### Prioridad de calidad

Agrupa variantes por identidad y nombre base, y sirve **una**. `0` = no servir
nunca esa calidad; `1` = último recurso. Con tabla vacía se usan las de fábrica
(4K > Full HD > HD > sin etiqueta > SD).

## Migración desde `config.ini`

```bat
python daemon.py migrate-config           :: crea daemon.toml + weteve.ini
python daemon.py migrate-config --check   :: comprueba que coinciden
```

`config.ini` se conserva; bórralo cuando compruebes que todo va.
