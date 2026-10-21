# Especificación: Gestor de listas (xTeVe/Threadfin en IPTVViewer)

> Resumen de referencia de usuario. La especificación técnica completa vive en
> `odd/tasks/list-manager.md` (contrato ODD).

## Qué es

Un submenú **"Mis Listas → Gestor de listas"** que replica en IPTVViewer la
funcionalidad esencial de [xTeVe](https://github.com/xteve-project/xTeVe) y
[Threadfin](https://github.com/Threadfin/Threadfin): filtrar listas M3U,
asociarlas a guías EPG (XMLTV) y **servir el resultado por web** para usarlo desde
cualquier reproductor externo.

## Cómo funciona (3 pasos)

1. **Fuente**: eliges una de tus fuentes existentes (las de `config.ini`,
   `[source.N]`) como origen de canales y su EPG asociado.
2. **Filtros**: defines reglas sencillas — incluir o excluir canales por texto en
   su grupo o nombre (ej.: excluir todo lo que contenga "XXX"). Internamente se
   guardan con la sintaxis compatible de xTeVe (`{incluir}`, `!{excluir}`), así que
   el formato podrá crecer sin perder lo ya configurado.
3. **EPG**: la app asocia cada canal con su guía buscando primero por `tvg-id` y,
   si falla, por nombre normalizado (sin acentos ni mayúsculas) y por parecido de
   marca (con el número de canal como parte del nombre: `M+ LaLiga 2` no es
   `M+ LaLiga`). Cada canal tiene además una identidad única —el canal del
   proveedor, con su dial y su logo— aunque su parrilla venga de otra guía. Los
   canales que quedan sin guía se revisan en un editor donde la app propone
   sugerencias y tú confirmas la correcta; tu corrección se recuerda para siempre.

## El servicio web

Con un clic inicias una mini-web (por defecto `http://127.0.0.1:8080`) que
expone:

- `/m3u/weteve_<perfil>.m3u` — tu lista ya filtrada, lista para cargar en VLC u otro reproductor.
- `/epg.xml` — la guía correspondiente, solo con los canales de tu lista.

El grupo **Servicio web** del asistente permite elegir el alcance:

- **Solo este equipo (127.0.0.1)** — por defecto; nada accesible desde fuera.
- **Toda la red local (0.0.0.0)** — VLC de tu móvil, tablet u otro PC de la red
  puede cargar la lista con la IP de tu equipo (p. ej. `http://192.168.1.50:34000/m3u/weteve_0.m3u`).
  Al iniciar el servicio se muestra la URL navegable con tu IP real.

La elección se recuerda junto al puerto en `config.ini`. Si al iniciar con
acceso de red VLC no conecta, añade una regla de entrada en el Firewall de
Windows para el puerto elegido (el perfil estándar bloquea conexiones entrantes).

El servicio se puede parar cuando quieras y registra las peticiones en
`logs/list_manager_web.log`.

## Lo que no hace (de momento)

- No retransmite los vídeos (los reproductores van directos a la fuente original).
- No sustituye a Plex/Jellyfin como DVR ni emula tuners HDHR.
- No actualiza las fuentes automáticamente: se refrescan desde el gestor.

Estas tres cosas son las ampliaciones naturales si algún día hacen falta (fase 2).
