# weteve

Gestor de listas de televisión por internet. Toma una o varias listas de canales
(formato M3U, por URL o fichero) y sus guías de programación (XMLTV), aplica
filtros, casa cada canal con su guía, lo numera y **sirve el resultado por HTTP**
para que cualquier reproductor lo cargue tal cual.

Dos procesos, un solo motor: **el demonio** hace el trabajo (pipeline + servidor)
y **la ventana de escritorio** es su frontend. El demonio expone además un
endpoint MCP para que agentes de IA lo gobiernen.

## Qué hace

- **Cartera de fuentes**: las listas y las guías se definen una vez y cada perfil
  elige qué filas usa. Varias listas se unen en una sola salida; los canales
  repetidos entre listas no se deduplican (se ven con el nombre de su lista).
- **Filtros** por campos (`group-title`, `custom-filter`, `tvg-group`, `extgrp`,
  `source`) con `{incluir}` / `!{excluir}` y operador `and`/`or`.
- **Casado con la guía en cuatro fases**: identificador exacto → nombre
  normalizado → nombre base sin la etiqueta de calidad → parecido por palabras
  de marca (con el sufijo numérico obligatorio y margen sobre el segundo
  candidato). Lo que no casa se asigna a mano, y cada asignación se recuerda.
- **Ficha del canal**: todo lo que decides tú de un canal —número, nombre que ve
  el reproductor, logo, guía y si se sirve o no— se guarda por **identidad** del
  canal, así que sobrevive a que el proveedor cambie la dirección.
- **Guía de relleno**: los canales sin programación real se pueden servir con una
  guía sintética de bloques de una hora.
- **Duplicados por calidad**: de cada canal repetido (4K/Full HD/HD/SD) se sirve
  una sola variante, según la prioridad que fijes.
- **Numeración real**: el número sale del dial que publica la guía, no de la
  posición en la lista; los que no tienen se numeran a continuación.
- **Servidor propio**: una lista y una guía por perfil, más los logos, todo en un
  único puerto. Sin proxy: la lista apunta a las direcciones originales.
- **Refresco programado** de cada fuente (`refresh_min`) y **vigilancia del
  proveedor**: un muestreo barato de unos pocos canales dice si la línea sigue
  respondiendo antes de que falle el reproductor.
- **Instancia única** por fichero de datos, con candado del sistema.

## Arquitectura

```
main.py                    ventana de escritorio (frontend del demonio)
daemon.py                  demonio: run | status | stop | install-task | migrate-config
src/
├── application/
│   ├── list_manager/      pipeline: filtros, casado de guía, calidad, fichas,
│   │                      playlist y guía de salida, muestreo del proveedor
│   └── services/daemon/   motor: servicio de dominio, supervisor, REST, MCP
├── domain/                entidades y puertos (canal, guía, playlist)
└── infrastructure/
    ├── adapters/          lectores de M3U y XMLTV (+ comprimidos)
    ├── client/            cliente REST, codec de configuración, lanzador, candado
    ├── ui/                ventana y paneles: perfiles, mapeo, cartera, estado
    ├── utils/             extracción de guías comprimidas
    └── web/               servidor único: REST, MCP, listas, guías y logos
tests/                     prueba automática (unitaria y de extremo a extremo)
```

El demonio es el dueño del estado: ejecuta el pipeline, sirve las listas y
publica un snapshot que la ventana pinta. La ventana nunca calcula nada; pide y
ordena por la API.

## Requisitos

- Sistema: Windows 10/11 (es el que se ha probado; el lanzador del demonio usa
  procesos sin consola propios de ese sistema).
- Python 3.11 o superior.
- `tar.exe` del sistema o una herramienta de descompresión si la guía viene en
  formato `.7z` (los `.gz` y `.zip` los maneja Python).

## Puesta en marcha

```powershell
cd <ruta-del-proyecto>
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

El demonio se arranca desde la propia ventana o a mano:

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
| `daemon.toml` | cartera de fuentes y perfiles (reglas, fichas, prioridad) | el demonio |
| `weteve.ini` | `[DAEMON]`: host, puerto, token y arranque | la ventana |

`config.ini` es el formato **viejo**: solo se lee para migrar
(`python daemon.py migrate-config`). Detalle de cada clave, del esquema TOML y de
la migración: `docs/diataxis/referencia-configuracion.md`.

## Uso

```powershell
.venv\Scripts\python.exe main.py            # ventana
.venv\Scripts\python.exe daemon.py status   # exit 0 = vivo
.venv\Scripts\python.exe daemon.py stop
```

El demonio sirve todo en un único puerto (`control_port`, 34400 por defecto):

- `/m3u/weteve_<perfil>.m3u` — la lista filtrada, numerada y con la guía asociada
- `/xmltv/weteve_<perfil>.xml` — la guía de ese perfil
- `/logos/<fichero>` — los logos que usa la lista
- `/api/v1/...` — API REST para la ventana y otros clientes
- `/mcp` — protocolo MCP para agentes

## Calidad

La configuración de las herramientas vive en `pyproject.toml`:

```powershell
.venv\Scripts\activate
ruff check .          # lint
mypy                  # tipos
pytest -q             # suite completa (excluye los marcados network)
pytest -m network -q  # los que salen a internet y leen las fuentes URL
```

Las pruebas de extremo a extremo arrancan un demonio real con puertos efímeros y
datos de ejemplo: no tocan tu configuración ni necesitan red.

## Documentación

- `docs/diataxis/` — **documentación canónica**: puesta en marcha, API,
  configuración, arquitectura y cómo trabajar en el proyecto.
- `docs/daemon.md` — el demonio: ciclo de vida, guardado, estado y protocolos.
- `docs/list-manager.md` y `docs/especificacion-gestor-listas.md` — especificación
  funcional de referencia.
- `EXTRACCION.md` — procedencia del código (documento histórico).
