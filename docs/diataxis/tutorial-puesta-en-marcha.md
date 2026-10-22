# Puesta en marcha (10 minutos)

Al final tendrás el demonio sirviendo una lista M3U y su guía en un único puerto,
y la ventana para configurarla.

## 1. Requisitos

- Windows (probado en Windows 11), Python 3.11.
- Opcional: `es.exe` (Everything) solo para los atajos de búsqueda del repo.

## 2. Instalar

```bat
cd <ruta-del-proyecto>
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 3. Configurar

Hay **dos** ficheros, con escritores distintos:

- `daemon.toml` — datos: cartera de fuentes y perfiles. Lo escribe el demonio.
- `weteve.ini` — cómo escucha: host, puerto, token y arranque. Lo escribe la GUI.

Lo mínimo de `weteve.ini`:

```ini
[DAEMON]
control_host = 127.0.0.1
control_port = 34400
mcp_token =
autostart = 1
stop_on_exit = 0
```

Un `daemon.toml` mínimo con una lista y un perfil:

```toml
version = 2

[[sources]]
id = "lista1"
name = "mi-lista"
kind = "m3u"
source_type = "url"
location = "http://proveedor/lista.m3u"
refresh_min = 720
enabled = true

[[targets]]
id = "0"
name = "salon"
port = 34400
bind = "127.0.0.1"
starting_number = 1
m3u_sources = ["lista1"]
epg_sources = []
rules = ["group-title:ESPAÑA:0:and"]
```

Si vienes del `config.ini` viejo: `python daemon.py migrate-config` (y
`--check` para comprobar que coincide).

## 4. Arrancar

```bat
.venv\Scripts\python.exe daemon.py run
```

Solo puede haber **un** demonio por `daemon.toml`: el segundo proceso sale con
`ya está en marcha en host:puerto` (exit 0) sin abrir nada.

## 5. Comprobar que sirve

```bat
curl http://127.0.0.1:34400/m3u/weteve_0.m3u
curl http://127.0.0.1:34400/xmltv/weteve_0.xml
```

La cabecera del M3U anuncia la guía (`url-tvg`), así que la app del móvil puede
descargarla sola.

## 6. Usar la GUI (opcional)

```bat
.venv\Scripts\python.exe main.py
```

Pestañas: Estado, Listas IPTV, Filtros iptv, Listas EPG, Mapeado IPTV ↔ EPG,
Catálogo de la guía, Configuración, Logs. La ventana es un **cliente** del
demonio: sin demonio no sirve la lista, aunque sí deja editar y guardar.

## 7. Dejarlo permanente

```bat
.venv\Scripts\python.exe daemon.py install-task
```

Registra la tarea `weteve-daemon` al iniciar sesión. Para pararlo:
`python daemon.py stop`.
