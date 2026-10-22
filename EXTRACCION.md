# Extracción del gestor de listas desde IPTVViewer

> **Documento histórico** (2026-10-08). Describe el momento de la extracción;
> el formato de configuración que cita (`config.ini`) es el viejo. Estado
> vigente: `docs/diataxis/` (configuración: `daemon.toml` + `weteve.ini`).

Fecha: 2026-10-08. Origen: `K:/IPTVViewer` (commit `dca2a0b`). El código se
**movió, no se borró**: todo lo referente al gestor de listas vive ahora aquí.

## Qué se ha hecho

1. **Creado `<ruta-del-proyecto>`** como aplicación independiente con la misma estructura
   de paquetes (`src/application`, `src/domain`, `src/infrastructure`) para que
   los imports `from src....` funcionen sin reescritura.

2. **Copiado a `<ruta-del-proyecto>\src\`**:
   - `application/services/list_manager/` — pipeline completo (models,
     filter_engine, epg_matcher, pipeline, playlist_builder)
   - `infrastructure/ui/list_manager_dialog.py` — wizard Qt (3 pasos + web)
   - `infrastructure/web/playlist_server.py` — mini-servidor HTTP local
   - `infrastructure/adapters/file_m3u_repository.py` y `xmltv_repository.py`
   - `infrastructure/utils/sevenzip.py` (necesario para EPGs .7z/.zip)
   - `domain/entities/` (channel, epg, playlist) y `domain/ports/` (i_epg_repo,
     i_playlist_repo) — compartidos con el visor, copiados para desacoplar

3. **Copiado a `<ruta-del-proyecto>\tests\`**: los 4 ficheros de test del gestor
   (`test_list_manager_{epg_matcher,filter_engine,pipeline,server}.py`) y los
   fixtures (`tests/fixtures/list_manager/`).

4. **Copiado a `<ruta-del-proyecto>\docs\`**: `list-manager.md` (spec odd/tasks) y
   `especificacion-gestor-listas.md` (referencia Diataxis).

5. **Eliminado de IPTVViewer** (mismo código, ya está aquí):
   - `src/application/services/list_manager/` (directorio completo)
   - `src/infrastructure/ui/list_manager_dialog.py`
   - `src/infrastructure/web/` (directorio completo, solo contenía esto)
   - `tests/test_list_manager_*.py` (4 ficheros) y `tests/fixtures/list_manager/`
   - `odd/tasks/list-manager.md`
   - `docs/diataxis/referencia/especificacion-gestor-listas.md`

6. **Desconectado IPTVViewer del gestor**:
   - `main.py`: import de `FilterRule`, parsing de secciones
     `listmanager.target.N`, clave `listmanager_targets` en `load_config()` y su
     serialización en `save_config()`.
   - `main_window.py`: submenú "Gestor de listas", `_update_list_manager_menu()`
     y `_open_list_manager()`.
   - `config.ini` del usuario conserva la sección `[listmanager.target.0]`;
     IPTVViewer ahora la ignora (no la lee ni la escribe). Bórrala a mano si
     quieres limpiarla.

7. **Creado `main.py` standalone para weteve**: carga `config.ini` (secciones
   `listmanager.target.N`, formato idéntico al de IPTVViewer), abre el wizard y
   persiste el target al aceptar. Logging a `logs/weteve.log`.

## Adaptaciones para el modo standalone

- `ListManagerDialog`: el parámetro `sources` ahora es opcional (`None` = sin
  listas de app anfitriona) y el modo de origen es siempre "custom" (URL o
  fichero propio). Se quitó el selector "Listas de la aplicación".
- weteve no tiene reproductores ni proxy: solo el pipeline de listas y el
  servidor web. Lo demás se quedó en IPTVViewer.

## Entorno del que viene (IPTVViewer)

- Python 3.11 + PyQt6 (UI), arquitectura hexagonal (application/domain/
- infrastructure) con tests pytest/pytest-qt.
- El gestor nació como feature "xTeVe interno" (spec en `docs/list-manager.md`):
  fuente M3U -> filtros sintaxis xTeVe -> match EPG -> servido por mini-web.
- La extracción se hizo con la suite de IPTVViewer en verde (215 tests OK tras
  quitar el gestor).

## Dependencias de weteve

Directas (`requirements.txt`): PyQt6>=6.5, requests>=2.28, psutil>=5.9,
pytest/pytest-qt (dev), ruff/mypy (dev).

- `requests` la usan los repositorios M3U/XMLTV para descargar por URL.
- `psutil` la usa `playlist_server.find_port_owner()` (opcional en runtime).
- No hay dependencia de VLC/mpv/Tor: eso es del visor, no del gestor.

## Verificación tras la extracción

- IPTVViewer: `pytest tests/ -q` → **215 passed** (ya sin tests del gestor).
- weteve: `pytest tests/ -q` → **67 passed** + 2 fallos E2E HTTP `WinError 10054`
  (ConnectionResetError en loopback). Es el filtro de red local conocido del
  equipo (memoria `env/loopback-firewall`), no código: los RSTs ambientales
  varían entre ejecuciones y afectan por igual a cualquier socket loopback.

## Cómo continuar el desarrollo

```powershell
cd <ruta-del-proyecto>
.venv\Scripts\activate      # venv ya creado con las dependencias instaladas
pytest tests/ -q            # suite en verde (salvo flakes loopback)
python main.py              # arranca el wizard
```

Pendiente razonable para evolucionar weteve:
- Gestión de múltiples targets desde la UI (hoy el `main.py` guarda solo el
  último target aceptado en `listmanager.target.0`).
- Servidor persistente (hoy el wizard es modal: al cerrar, se apaga el servidor).
- Empaquetado propio (PyInstaller) y repo git propio.
