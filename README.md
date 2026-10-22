# weteve

Gestor de listas IPTV independiente, extraído de [IPTVViewer](K:/IPTVViewer).
Toma una o varias listas M3U (URL o fichero), aplica filtros en sintaxis xTeVe,
asocia la guía EPG (XMLTV) y sirve el resultado por un servidor web local
(`/m3u/weteve_<perfil>.m3u` y `/xmltv/weteve_<perfil>.xml`).

Dos procesos, un solo motor: **el demonio** hace el trabajo (pipeline + servidor)
y **la ventana Qt** es su frontend (habla REST). El demonio expone además un
endpoint MCP para gobernarlo por agentes.

## Arquitectura

```
main.py                    ventana Qt (frontend del demonio)
daemon.py                  demonio: run | status | stop | install-task | migrate-config
src/
├── application/
│   ├── list_manager/      pipeline: filtros, match EPG, calidad, fichas, playlist/XMLTV
│   └── services/daemon/   motor: TargetService, TargetSupervisor, REST, MCP
├── domain/                entidades (Channel, EPGData, Playlist) y puertos
└── infrastructure/
    ├── adapters/          file_m3u_repository, xmltv_repository
    ├── client/            cliente REST, codec de config, lanzador, candado
    ├── ui/                widgets Qt (diálogo de perfiles, mapeado, cartera)
    ├── utils/             sevenzip (EPGs comprimidos)
    └── web/               servidor único: REST, MCP, playlists y logos
tests/                     1003 tests (unit + e2e con demonio real)
```

## Requisitos

- Windows 10/11 (probado aquí), Python 3.11+
- `tar.exe` de Windows o 7-Zip instalado (solo si el EPG viene en .7z; los .gz
  y .zip los maneja Python directamente)

## Puesta en marcha

```powershell
cd K:\weteve
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

El demonio se arranca desde la propia ventana si marcas `autostart`, o a mano:

```powershell
.venv\Scripts\python.exe daemon.py run
```

Opcional: copia `daemon.toml.example` a `daemon.toml` (datos: cartera y perfiles)
y `weteve.ini.example` a `weteve.ini` (arranque: host, puerto y token) para
empezar con configuración previa.

## Configuración

Dos ficheros, con **un escritor cada uno**:

| Fichero | Contenido | Escribe |
|---|---|---|
| `daemon.toml` | cartera de fuentes y perfiles (reglas, mapeos, prioridad) | el demonio |
| `weteve.ini` | `[DAEMON]`: host, puerto, token y arranque | la GUI |

`config.ini` es el formato **viejo**: solo se lee para migrar
(`python daemon.py migrate-config`). Detalle de cada clave, del esquema TOML y de
la migración: `docs/diataxis/referencia-configuracion.md`.

Las reglas de filtro viajan en sintaxis xTeVe (`campo:{patrón}:case`): texto
plano incluye, `{texto}` incluye canales que cumplan, `!{texto}` excluye.

### Mapeo EPG y canales sin guía

Si el perfil tiene fuente EPG, **un canal sin guía asignada no se sirve**: así
se evita una lista de canales que el reproductor muestra sin programación. La
regla no se aplica cuando no hay fuente EPG configurada.

En la pestaña «Mapeado IPTV <-> EPG» del diálogo:

- **EPG asignado**: desplegable con los canales de la guía. La etiqueta se
  muestra sin el sufijo `.TV` (p. ej. `La1`), pero lo que se guarda es el
  channel-id real (`La1.TV`). También se puede teclear un id que la guía no
  anuncie.
- **`Dummy 1h`** (primera opción con nombre, tras la vacía): guía de relleno
  sintética — bloques de 1 h durante 7 días, con el nombre del canal como
  título. Genera un id propio por canal (`weteve-dummy-…`), así que se puede
  rescatar canal a canal.
- **Activo**: casilla derivada (sin guía asignada o apagado a mano). Apagar un
  canal lo saca de la lista y lo persiste como canal desactivado. Reactivar una
  fila **sin** guía no es posible: la casilla vuelve sola a su estado real, y
  primero hay que asignarle `Dummy 1h` o un canal de la guía. Al asignarle guía
  se limpia también el apagado manual, para que elegir `Dummy 1h` no parezca no
  hacer nada.

> Los canales que la coincidencia automática no empareja son muchos en listas
> de proveedor (nombres como «La 1 HD» frente a ids como `La1.TV`). Con la regla
> activa, la lista servida se reduce hasta que se asignan: usa el desplegable o
> `Dummy 1h` para recuperarlos.

## Servidor web

El demonio sirve todo en un único puerto (`control_port`, 34400 por defecto):

- `/m3u/weteve_<perfil>.m3u` — la lista filtrada, numerada y con `url-tvg`
- `/xmltv/weteve_<perfil>.xml` — la guía asociada a ese perfil
- `/logos/<fichero>` — los logos que sirve la lista
- `/api/v1/...` — REST para la GUI y otros frontends
- `/mcp` — JSON-RPC MCP para agentes

## Calidad

La configuración de las herramientas vive en `pyproject.toml` (ruff, mypy y
pytest; no quedan `pytest.ini` ni `setup.cfg`):

```powershell
.venv\Scripts\activate
ruff check .          # lint (ruleset fijado en [tool.ruff])
mypy                  # tipos ([tool.mypy]: files, explicit_package_bases)
pytest -q             # suite completa (excluye los marcados network)
pytest -m network -q  # los que salen a internet y leen las fuentes URL
```

Los e2e del demonio (`tests/test_daemon_e2e_*.py`) arrancan un demonio real con
puertos efímeros y las fixtures M3U/XMLTV: no tocan tu `daemon.toml` ni necesitan
red. `.github/workflows/ci.yml` ejecuta ruff, mypy, compileall y pytest en
Windows/Python 3.11.

## Documentación

- `docs/diataxis/` — **documentación canónica**: puesta en marcha, API,
  configuración, arquitectura y cómo trabajar en el proyecto.

### Documentación heredada

- `docs/list-manager.md` — especificación original del gestor de listas
- `docs/especificacion-gestor-listas.md` — especificación funcional de referencia
- `EXTRACCION.md` — cómo se extrajo weteve de IPTVViewer (histórico)
