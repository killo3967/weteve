# weteve

Gestor de listas IPTV independiente, extraído de [IPTVViewer](K:/IPTVViewer).
Toma una lista M3U (URL o fichero), aplica filtros en sintaxis xTeVe, asocia la
guía EPG (XMLTV) y sirve el resultado por un mini-servidor web local
(`playlist.m3u` y `epg.xml`).

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
| `port` / `bind` | Puerto y dirección del servidor web (8080 / 127.0.0.1) |

Sintaxis de filtros (compatible xTeVe): texto plano = incluir si aparece;
`{texto}` = incluir canales que cumplan; `!{texto}` = excluir.

## Servidor web

Una vez aceptado el wizard, el servidor sirve en `http://127.0.0.1:<port>`:

- `/playlist.m3u` — la lista filtrada y mapeada
- `/epg.xml` — la guía EPG asociada

## Tests

```powershell
.venv\Scripts\activate
pytest tests/ -q
```

Nota: los tests E2E HTTP (`test_list_manager_server.py::TestHttpEndToEnd`)
pueden fallar con `WinError 10054` si un filtro de red (Adguard/WFP) está
inyectando resets en loopback. Es ambiental, no de código: los tests unitarios
(route + sockets crudos) y el resto de la suite son estables.

## Documentación heredada

- `docs/list-manager.md` — especificación original (odd/tasks)
- `docs/especificacion-gestor-listas.md` — especificación Diataxis de referencia
