# Especificación: Gestor de listas

> Resumen de referencia de usuario. La especificación técnica completa vive en
> `docs/list-manager.md` (contrato ODD original) y el estado vigente en
> `docs/diataxis/`.

## Qué es

**weteve** hace lo esencial de un gestor de listas: filtrar listas M3U,
asociarlas a guías de programación (XMLTV) y **servir el resultado por web** para
usarlo desde cualquier reproductor. Funciona como aplicación independiente: un
demonio hace el trabajo y una ventana de escritorio lo configura.

## Cómo funciona (3 pasos)

1. **Fuente**: eliges filas de tu cartera (bloques `[[sources]]` de `daemon.toml`)
   como origen de canales y de guía. Varias listas se unen en una sola salida.
2. **Filtros**: defines reglas sencillas — incluir o excluir canales por texto en
   su grupo o nombre (ej.: excluir todo lo que contenga "XXX"). Internamente se
   guardan con la misma sintaxis de filtros (`{incluir}`, `!{excluir}`), así que
   el formato puede crecer sin perder lo ya configurado.
3. **EPG**: la app asocia cada canal con su guía en cuatro fases: `tvg-id`
   exacto → nombre normalizado exacto (sin acentos ni mayúsculas) → nombre base
   sin la etiqueta de calidad (`LA 1 HD` es `LA 1`) → parecido por palabras de
   marca, con dos reglas duras: el sufijo numérico tiene que coincidir
   (`LaLiga 2` no es `LaLiga`) y el ganador necesita margen sobre el
   segundo candidato. Cada canal tiene además una **identidad única** —el canal
   del proveedor, con su dial y su logo— aunque su parrilla venga de otra guía;
   de los duplicados se sirve una variante según la prioridad de calidad. Los
   canales que quedan sin guía se revisan en el editor de mapeado, donde la app
   propone sugerencias y tú confirmas la correcta; tu corrección se recuerda.

## El servicio web

El demonio sirve todo en un único puerto (`control_port`, 34400 por defecto) y
expone:

- `/m3u/weteve_<perfil>.m3u` — tu lista ya filtrada, lista para cargar en
  cualquier reproductor.
- `/xmltv/weteve_<perfil>.xml` — la guía correspondiente, solo con los canales de
  tu lista.
- `/logos/<fichero>` — los logos que usa la lista.

El alcance se elige en la pestaña **Configuración** del editor de perfiles:

- **Solo este equipo (127.0.0.1)** — por defecto; nada accesible desde fuera.
- **Toda la red local (0.0.0.0)** — un reproductor de tu móvil, tablet u otro PC
  de la red puede cargar la lista con la IP de tu equipo (p. ej.
  `http://192.0.2.50:34400/m3u/weteve_0.m3u`). Fuera de loopback el token es
  obligatorio: el demonio no arranca sin él.

La elección se guarda en `weteve.ini` (`[DAEMON]` `control_host`/`control_port`).
Si con acceso de red el reproductor no conecta, añade una regla de entrada en el
cortafuegos para el puerto elegido (el perfil estándar bloquea entrantes).

El servicio se puede parar cuando quieras y registra las peticiones en
`logs/weteve-daemon.log`.

## Lo que no hace (de momento)

- No retransmite los vídeos (los reproductores van directos a la fuente original).
- No hace de grabador ni emula sintonizadores de red.

Estas dos cosas son las ampliaciones naturales si algún día hacen falta (fase 2).
