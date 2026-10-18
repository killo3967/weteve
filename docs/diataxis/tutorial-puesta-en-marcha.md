# Puesta en marcha (10 minutos)

Objetivo: tener weteve sirviendo una lista filtrada con guía EPG y comprobarlo
desde un reproductor IPTV. No hace falta tocar código.

## 1. Requisitos

- Windows 10/11 (probado ahí; el resto de plataformas no se prueba).
- Python 3.11.
- Una lista M3U (URL o fichero) y, si quieres guía, un XMLTV (`.xml`, `.xmltv`,
  `.gz`).

## 2. Instalar

```powershell
cd K:\weteve
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Configurar

Copia `config.ini.example` a `config.ini` si no existe y edita la sección del
target:

```ini
[DAEMON]
control_host = 127.0.0.1
control_port = 34400
mcp_port = 34401
mcp_token =

[listmanager.target.0]
name = mi lista
custom_m3u = C:/ruta/lista.m3u
custom_epg = C:/ruta/guia.xml
rules =
```

Detalle de cada clave: [`referencia-configuracion.md`](referencia-configuracion.md).

## 4. Arrancar

```powershell
python daemon.py run      # motor: pipeline + playlist en :9999
python main.py            # GUI (opcional; sin demonio no muestra datos)
```

Al arrancar, el demonio migra configs antiguas de fuente única a la cartera y
publica **todo en un solo puerto** (`[DAEMON] control_port`, 34400):

- `http://127.0.0.1:34400/weteve/0/playlist.m3u`
- `http://127.0.0.1:34400/weteve/0/epg.xml`
- alias cortos: `/weteve/playlist.m3u` y los heredados `/playlist.m3u` / `/epg.xml`

## 5. Comprobar que sirve

```powershell
python scripts/healthcheck.py
```

Debe terminar con `Resumen: N OK, 0 FAIL`. Comprueba la API de control, el MCP,
el estado del target, la playlist, el EPG y cada fuente de la cartera.

También puedes apuntar VLC/Jellyfin a la URL de `playlist.m3u`.

## 6. Usar la GUI (opcional)

Pestañas: **Estado** (semáforo 🟢/🔴 del demonio y URL de cada servicio:
control, REST, MCP, playlist y EPG), **Listas IPTV**, **Listas EPG**, **Filtros**
(con vista previa en vivo), **Mapeado EPG**, **Configuración**, **Logs**.

El botón «Iniciar demonio» lo lanza **sin ventana de cmd**: los logs quedan en
`logs/weteve-daemon.log`.

- Guardar escribe **en el demonio** por la API: no hay guardado local.
- «Iniciar servicio» arranca/para el servidor del target, que vive en el demonio.
- Casillas de `Configuración`: arrancar el demonio al abrir y pararlo al cerrar
  (solo si lo arrancó la ventana).

## 7. Dejarlo permanente

```powershell
python daemon.py install-task   # tarea al iniciar sesión
python daemon.py status         # ¿está vivo?
python daemon.py stop
```
