# Arquitectura y decisiones

weteve tiene tres piezas: un **demonio** que posee los datos y sirve la lista,
una **ventana Qt** que es su frontend, y una **capa de dominio** *Qt-free* que
hace el trabajo (parsear, filtrar, emparejar, numerar).

## Piezas

```
main.py ── Qt ─────────────► ApiClient ──HTTP──┐
                                               │
daemon.py ──► TargetService ──► TargetSupervisor ──► ListManagerPipeline
                    │                   │                    │
              TargetStore          PlaylistServer      filtros · EPG · calidad
                    │                   │
               daemon.toml          /m3u · /xmltv · /logos
```

- **Dominio** (`src/domain`, `list_manager`): entidades y pipeline puro; se
  testea sin red ni Qt.
- **Servicios** (`daemon/`): supervisor (ciclo de vida + snapshot), servicio de
  dominio (`TargetService`) y adaptadores (REST, MCP, control).
- **Infraestructura**: HTTP único, cliente de la API, persistencia y widgets.

## Decisiones y por qué

### Un fichero por escritor

`daemon.toml` (datos) lo escribe solo el demonio; `weteve.ini` (arranque) solo
la GUI. Así no hay dos procesos reescribiendo el mismo TOML y perdiendo cambios.

### Lecturas desde snapshot; escrituras con recarga diferida

Las lecturas de estado no reejecutan el pipeline: el supervisor guarda el último
`PipelineResult`. Un guardado puede mandar varios cambios con `reload: false` y
reejecutar **una vez** al final (el pipeline tarda segundos con listas grandes).

### Dos locks en el supervisor

`_lock` protege el estado (respuestas rápidas) y `_run_lock` serializa la
ejecución del pipeline, que no es reentrante. Responder estado no espera a una
recarga.

### Instancia única

El puerto solo impedía dos demonios en el **mismo** puerto. Ahora el arranque
toma un bloqueo del sistema sobre `weteve.lock` y anota `pid host:puerto` en
`weteve.pid` antes de leer la configuración; si ya está tomado, consulta
`/health` y sale con `ya está en marcha en …` (exit 0). Cubre también dos
`control_port` distintos, que escribirían el mismo `daemon.toml`.

### Un solo puerto y un solo bind

Control, REST, MCP, playlists y logos comparten `control_port` (34400 por
defecto). Menos firewalls, menos URLs que explicar. El host se elige en un
desplegable (loopback, IP de la red, otras interfaces, `0.0.0.0`); con `0.0.0.0`
se **anuncia** la IP de la red, porque `0.0.0.0` no es una dirección de destino.

### Identidad del canal

Cada guía bautiza al mismo canal a su manera (`LaLigaTVHD`, `LALIGA TV HD`,
`LaLiga`, `LA LIGA`). La identidad se resuelve contra la guía que **publica
dial** (solo algunas guías lo publican) con un casador por marca: el sufijo
numérico tiene
que coincidir y el ganador necesita margen. Las guías XMLTV solo traen logo, y
tomarlas por canónicas partía el canal en dos.

### Ficha del canal

Lo que el usuario fija a mano (dial, nombre mostrado, logo) vive en
`[targets.channels."<identidad>"]`, con la identidad como clave (código de guía,
o `url-<hash>` / `name-<base>` si no hay). El EPG asignado y el apagado siguen en
`overrides`/`disabled` por url: migrarlos es un cambio aparte, no una capa nueva.

### Dos nombres

El **interno** (el de la lista) manda en dedupe y en los pesos de calidad; el
**mostrado** es cosmético y sale del nombre de la guía (`LA 1 HD` → `LA 1`).
Editar el interno cambiaría qué variante se sirve.

### Guía ligera

El `epg.xml` con descripciones pesa ~9 MB y muchas televisiones cortan la guía
por tamaño (síntoma: EPG hasta cierto canal y nada después). Sin `<desc>` ni
`<category>` baja a ~3 MB y la ven entera. Es un campo del perfil, activo por
defecto.

### Revisión por perfil

La ventana no puede permitirse pedir 855 filas cada segundo y medio. El demonio
lleva un contador `revision` por perfil que sube con cada guardado o recarga; la
GUI sondea `/status` (cientos de bytes) y solo repinta la tabla cuando cambia.

### Fail closed en lo peligroso

Guardar fuera de loopback sin token no se permite (y el demonio tampoco
arrancaría). Un dial repetido no se guarda. Un logo que no existe o no se puede
descargar es un error, no un aviso silencioso.

## Flujo de un «Guardar»

1. La GUI arma un lote: cartera, fuentes del perfil, ajustes, reglas, mapeos,
   desactivados y fichas, todo con `reload: false`.
2. Un hilo lo manda a la API; el demonio guarda una vez y reejecuta el pipeline
   al final (`run`).
3. El supervisor actualiza el snapshot y sube `revision`; la GUI repinta el
   mapeado y el estado.

## Modelo de datos del canal

`Channel` (de la lista) → filtros → `MappedChannel` (con `matched_epg_id` para la
parrilla y `canonical_id` para la identidad) → dedupe por calidad → `_finalize`
aplica dial, nombre mostrado y logo → M3U + XMLTV.
