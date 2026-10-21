# weteve

Gestor de listas IPTV independiente, extraído de [IPTVViewer](K:/IPTVViewer).
Toma una lista M3U (URL o fichero), aplica filtros en sintaxis xTeVe, asocia la
guía EPG (XMLTV) y sirve el resultado por un mini-servidor web local
(`/m3u/weteve_<perfil>.m3u` y `/xmltv/weteve_<perfil>.xml`).

## Estado

Código recién extraído de IPTVViewer (ver `EXTRACCION.md`). Funcional con el
flujo completo del wizard; pendiente de evolución como aplicación propia.

## Arquitectura

```
src/
├── application/services/list_manager/   # Pipeline: filtros, match EPG, salida
│   ├── models.py          # FilterRule, PipelineTarget (sintaxis xTeVe)
│   ├── filter_engine.py   # evaluate_channel: reglas sobre canales
│   ├── epg_matcher.py     # Matching fuzzy canal <-> EPG
│   ├── pipeline.py        # ListManagerPipeline (orquestación)
│   └── playlist_builder.py# build_m3u / build_xmltv (salidas)
├── domain/                # Entidades (Channel, EPGData, Playlist) y puertos
├── infrastructure/
│   ├── adapters/          # file_m3u_repository, xmltv_repository
│   ├── utils/             # sevenzip (extracción .7z para EPGs comprimidos)
│   ├── ui/                # list_manager_dialog (wizard Qt 3 pasos)
│   └── web/               # playlist_server (ThreadingHTTPServer local)
tests/                    # 69 tests del gestor (unit + E2E HTTP)
main.py                   # Punto de entrada Qt + config.ini
```

## Requisitos

- Windows 10/11 (probado aquí), Python 3.11+
- `tar.exe` de Windows o 7-Zip instalado (solo si el EPG viene en .7z/.zip/.gz
  los .gz y .zip los maneja Python directamente)

## Puesta en marcha

```powershell
cd K:\weteve
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Opcional: copia `config.ini.example` a `config.ini` para arrancar con un target
preconfigurado.

## Configuración

`config.ini`, una sección `[listmanager.target.N]` por resultado:

| Clave | Descripción |
|---|---|
| `name` | Nombre del target |
| `custom_m3u` | URL o ruta del M3U de origen |
| `custom_epg` | URL o ruta del XMLTV (opcional) |
| `rules` | Filtros xTeVe separados por `\|` (`campo:patrón:case`) |
| `epg_overrides` | Asignaciones manuales `url::canal-id` separadas por `;;` |
| `disabled_channels` | URLs apagadas a mano, separadas por `\|\|` (solo decisión del usuario) |
| `port` / `bind` | Puerto y dirección del servidor web (8080 / 127.0.0.1) |

Sintaxis de filtros (compatible xTeVe): texto plano = incluir si aparece;
`{texto}` = incluir canales que cumplan; `!{texto}` = excluir.

### Mapeo EPG y canales sin guía

Si el target tiene fuente EPG, **un canal sin guía asignada no se sirve**: así
se evita una lista de canales que el reproductor muestra sin programación. La
regla no se aplica cuando no hay fuente EPG configurada.

En la pestaña «Mapeado EPG» del editor:

- **EPG asignado**: desplegable con los canales de la guía. La etiqueta se
  muestra sin el sufijo `.TV` (p. ej. `La1`), pero lo que se guarda es el
  channel-id real (`La1.TV`). También se puede teclear un id que la guía no
  anuncie.
- **`Dummy 1h`** (primera opción con nombre, tras la vacía): guía de relleno sintética —
  bloques de 1 h durante 7 días, con el nombre del canal como título. Genera un
  id propio por canal (`weteve-dummy-…`), así que se puede rescatar canal a
  canal.
- **Activo**: casilla derivada (sin guía asignada o apagado a mano). Apagar un
  canal lo saca de la lista y lo persiste en `disabled_channels`. Reactivar una
  fila **sin** guía no es posible: la casilla vuelve sola a su estado real, y
  primero hay que asignarle `Dummy 1h` o un canal de la guía. Al asignarle guía
  se limpia también el apagado manual, para que elegir `Dummy 1h` no parezca no
  hacer nada.

> Los canales que la coincidencia automática no empareja son muchos en listas
> de proveedor (nombres como «La 1 HD» frente a ids como `La1.TV`). Con la regla
> activa, la lista servida se reduce hasta que se asignan: usa el desplegable o
> `Dummy 1h` para recuperarlos.

## Servidor web

Una vez aceptado el wizard, el servidor sirve en `http://127.0.0.1:<port>`:

- `/m3u/weteve_<perfil>.m3u` — la lista filtrada, numerada y con `url-tvg`
- `/epg.xml` — la guía EPG asociada

## Calidad

La configuración de las herramientas vive en `pyproject.toml` (ruff, mypy y
pytest; no quedan `pytest.ini` ni `setup.cfg`):

```powershell
.venv\Scriptsctivate
ruff check .          # lint (ruleset fijado en [tool.ruff])
mypy                  # tipos ([tool.mypy]: files, explicit_package_bases)
pytest -q             # suite completa (excluye los marcados network)
pytest -m network -q  # los 6 que salen a internet y leen las fuentes URL
```

Los e2e del daemon (`tests/test_daemon_e2e_*.py`) arrancan un daemon real con
puertos efímeros y las fixtures M3U/XMLTV: no tocan tu `config.ini` ni necesitan
red. `.github/workflows/ci.yml` ejecuta ruff, mypy, compileall y pytest en
Windows/Python 3.11.

## Documentación

- `docs/diataxis/` — **documentación canónica**: puesta en marcha, API,
  configuración, arquitectura y cómo trabajar en el proyecto.

## Documentación heredada

- `docs/list-manager.md` — especificación original (odd/tasks)
- `docs/especificacion-gestor-listas.md` — especificación Diataxis de referencia
