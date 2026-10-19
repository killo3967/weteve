"""Guía de Movistar+ desde su API OTT (JSON, no XMLTV).

La fuente se declara en la cartera como ``movistar://epg`` (opcionalmente
``movistar://epg?span=7``). No es una URL pública de XMLTV: son dos endpoints
JSON, uno con el catálogo de canales —que trae ``Dial`` (el número de canal
real) y ``Logos`` (la imagen vigente)— y otro con la parrilla de cada canal.

Ese dial y ese logo no caben en XMLTV, así que viajan en
``EPGData.channel_info``: el pipeline los usa como origen de ``tvg-chno`` y de
``tvg-logo``. Es la razón por la que esta fuente se comporta distinto a una
guía XMLTV normal.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests

from src.domain.entities.epg import ChannelInfo, EPGData, Program
from src.domain.ports.i_epg_repo import IEPGRepository

logger = logging.getLogger(__name__)


class MovistarEPGRepository(IEPGRepository):
    """Adaptador de la guía de Movistar+ (canales + parrilla por canal).

    - ``span_days`` es lo que dura la parrilla que se pide por canal (la API
      devuelve ``span+1`` días desde hoy a las 00:00; el mínimo útil son 3).
    - La parrilla se pide en paralelo: una petición por canal y el catálogo
      tiene ~150 canales, así que en serie tardaría minutos.
    - Cachea la guía ya construida con el mismo criterio que el lector XMLTV
      (TTL), porque cambiar una regla de filtro reejecuta el pipeline pero no
      cambia la guía.
    """

    BASE_URL = "https://ottcache.dof6.com/movistarplus/webplayer/OTT"
    COMMON_PARAMS = "version=8&mdrm=true&tlsstream=true&demarcation=18"
    DEFAULT_SPAN_DAYS = 3
    MAX_SPAN_DAYS = 7
    DEFAULT_TTL_SECONDS = 300.0
    CHANNELS_TTL_SECONDS = 6 * 3600.0
    MAX_WORKERS = 8

    def __init__(self, span_days: int | None = None,
                 ttl_seconds: float | None = None,
                 max_workers: int | None = None,
                 timeout: float = 20.0) -> None:
        self._span_default = self._clamp_span(
            self.DEFAULT_SPAN_DAYS if span_days is None else span_days)
        self._ttl = (self.DEFAULT_TTL_SECONDS if ttl_seconds is None
                     else float(ttl_seconds))
        self._max_workers = max(1, int(max_workers or self.MAX_WORKERS))
        self._timeout = float(timeout)
        self._cache: dict[str, tuple[tuple, EPGData]] = {}
        self._channels_cache: tuple[tuple, list[dict]] | None = None
        self._uncached = 0

    # -- contrato del puerto -------------------------------------------------

    def load_epg(self, source: str, fresh: bool = False) -> EPGData:
        """Descarga canales y parrilla y los empaqueta como ``EPGData``.

        ``fresh=True`` ignora la caché: es lo que piden el planificador y el
        botón «Actualizar guías».
        """
        span = self._span_for(source)
        signature = self._signature(source, span)
        cached = None if fresh else self._cache.get(source)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            epg = self._fetch(span, fresh=fresh)
        except Exception as e:  # noqa: BLE001 - un fallo no debe tumbar el pipeline
            logger.error("Error cargando la guía de Movistar+ (%s): %s", source, e)
            return EPGData()
        self._cache[source] = (signature, epg)
        return epg

    # -- construcción --------------------------------------------------------

    def _fetch(self, span: int, fresh: bool = False) -> EPGData:
        canales = self._channels(fresh=fresh)
        if not canales:
            return EPGData()
        names: dict[str, str] = {}
        info: dict[str, ChannelInfo] = {}
        for canal in canales:
            code = str(canal.get("CodCadenaTv") or "").strip()
            if not code:
                continue
            names[code] = str(canal.get("Nombre") or code)
            info[code] = ChannelInfo(dial=self._dial(canal.get("Dial")),
                                     logo=self._logo(canal))
        programs = self._programs_for_all(tuple(names), span)
        logger.info("Guía Movistar+: %d canales, %d programas (span=%d)",
                    len(names), len(programs), span)
        return EPGData(programs=programs, channel_names=names,
                       channel_info=info)

    def _programs_for_all(self, codigos: tuple[str, ...], span: int) -> list[Program]:
        inicio = datetime.now().replace(hour=0, minute=0, second=0,
                                        microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
        programs: list[Program] = []
        fallos = 0
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futuros = {
                pool.submit(self._programs_for, code, inicio, span): code
                for code in codigos
            }
            for futuro in as_completed(futuros):
                code = futuros[futuro]
                try:
                    programs.extend(futuro.result())
                except Exception as e:  # noqa: BLE001 - un canal caído no es fatal
                    fallos += 1
                    logger.warning("Movistar+: sin parrilla para %s: %s", code, e)
        if fallos:
            logger.warning("Movistar+: %d canales sin parrilla en esta carga", fallos)
        return programs

    def _programs_for(self, code: str, inicio: str, span: int) -> list[Program]:
        url = (f"{self.BASE_URL}/epg?from={inicio}&span={span}&channel={code}"
               f"&{self.COMMON_PARAMS}")
        filas = self._get_json(url) or []
        programs: list[Program] = []
        for fila in filas:
            if not isinstance(fila, dict):
                continue
            start = self._epoch_ms(fila.get("FechaHoraInicio"))
            end = self._epoch_ms(fila.get("FechaHoraFin"))
            if start is None or end is None:
                continue
            programs.append(Program(
                title=str(fila.get("Titulo") or "Sin título"),
                start_time=start,
                end_time=end,
                channel_id=code,
                description=None,
                category=(fila.get("GeneroComAntena")
                          or fila.get("TipoContenido") or None),
            ))
        return programs

    def _channels(self, fresh: bool = False) -> list[dict]:
        signature = self._channels_signature()
        if (not fresh and self._channels_cache is not None
                and self._channels_cache[0] == signature):
            return self._channels_cache[1]
        url = f"{self.BASE_URL}/contents/channels?{self.COMMON_PARAMS}"
        datos = self._get_json(url) or []
        canales = [c for c in datos if isinstance(c, dict)]
        self._channels_cache = (signature, canales)
        return canales

    # -- utilidades ----------------------------------------------------------

    def _get_json(self, url: str):
        response = requests.get(url, timeout=self._timeout, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.movistarplus.es/programacion-tv",
            "Accept": "application/json",
        })
        response.raise_for_status()
        return response.json()

    def _span_for(self, source: str) -> int:
        """``span`` de la fuente (``movistar://epg?span=7``), o el de defecto."""
        try:
            query = parse_qs(urlparse(str(source)).query)
        except ValueError:
            return self._span_default
        valores = query.get("span") or []
        if not valores:
            return self._span_default
        try:
            return self._clamp_span(int(valores[0]))
        except (TypeError, ValueError):
            return self._span_default

    @classmethod
    def _clamp_span(cls, span: int) -> int:
        return max(1, min(cls.MAX_SPAN_DAYS, int(span)))

    def _signature(self, source: str, span: int) -> tuple:
        """Huella con fecha y TTL: la parrilla de hoy no es la de mañana."""
        if self._ttl <= 0:
            self._uncached += 1
            return ("sin-cache", self._uncached)
        hoy = datetime.now().date().isoformat()
        return ("movistar", hoy, span, int(time.monotonic() // self._ttl))

    def _channels_signature(self) -> tuple:
        if self._ttl <= 0:
            self._uncached += 1
            return ("sin-cache", self._uncached)
        return ("canales", int(time.monotonic() // self.CHANNELS_TTL_SECONDS))

    @staticmethod
    def _epoch_ms(valor) -> datetime | None:
        """Marca de tiempo en milisegundos (epoch) a ``datetime`` local."""
        try:
            return datetime.fromtimestamp(int(valor) / 1000)
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    @staticmethod
    def _dial(valor) -> int | None:
        try:
            dial = int(str(valor).strip())
        except (TypeError, ValueError):
            return None
        return dial if dial > 0 else None

    @staticmethod
    def _logo(canal: dict) -> str | None:
        """Logo vigente: el del canal y, si falta, el primero del catálogo."""
        logo = str(canal.get("Logo") or "").strip()
        if logo:
            return logo
        for entrada in canal.get("Logos") or []:
            if isinstance(entrada, dict):
                uri = str(entrada.get("uri") or "").strip()
                if uri:
                    return uri
        return None
