# Documentación de weteve

weteve convierte una lista IPTV en una lista **filtrada, numerada y con guía**
para un reproductor, con un demonio que la sirve y una ventana Qt que la
configura.

## Qué leer según lo que necesites

| Si quieres… | Lee |
|---|---|
| Instalarlo y verlo funcionando | [tutorial-puesta-en-marcha](tutorial-puesta-en-marcha.md) |
| Tocar el código sin romperlo | [como-trabajar-en-el-proyecto](como-trabajar-en-el-proyecto.md) |
| Entender por qué está hecho así | [explicacion-arquitectura](explicacion-arquitectura.md) |
| Saber qué endpoint o herramienta hay | [referencia-api](referencia-api.md) |
| Saber qué clave va en cada fichero | [referencia-configuracion](referencia-configuracion.md) |

## Vocabulario

- **Perfil**: una salida completa (lista + filtros + guía + numeración). En el
  código y en la API se llama `target`.
- **Ficha**: lo que el usuario ha fijado a mano para un canal — número, nombre
  visible, logo, guía y si se sirve o no. Va por **identidad** del canal, así
  que sobrevive a que el proveedor cambie la dirección. En el código,
  `ChannelProfile`.
- **Guía ligera**: `epg.xml` sin descripciones ni categorías (~3 MB en vez de
  9 MB). Activa por defecto.

## Estado verificado (2026-10-22)

| Comprobación | Resultado |
|---|---|
| `pytest -q` | 1022 tests en verde |
| `ruff check` | limpio |
| `mypy` | sin errores (142 ficheros) |
| Demonio | 146 canales servidos, 0 sin logo |

Informe de auditoría: `docs/forensic-audit/2026-10-21.md`.

## Mapa rápido del repositorio

```
main.py            ventana Qt (frontend del demonio)
daemon.py          proceso del demonio: run | status | stop | install-task | migrate-config
src/
  domain/          entidades y puertos, sin Qt ni red
  application/
    list_manager/  pipeline, filtros, matcher EPG, calidad, fichas, playlist/XMLTV
    services/      daemon (supervisor, API, MCP, servicio de dominio)
  infrastructure/
    client/        cliente de la API, cartera, config, lanzador, candado
    ui/            widgets Qt
    web/           servidor único: REST, MCP, playlists, logos
tests/             pytest; los e2e levantan un demonio real
assets/logos/      copia local de logos (ignorada por git)
docs/              documentación y auditorías
```
