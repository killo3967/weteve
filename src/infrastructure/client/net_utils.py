"""Direcciones para el demonio: escuchar y conectar no son lo mismo.

``control_host`` dice dónde **enlaza** el servidor (``0.0.0.0`` = todos los
interfaces). Pero ``0.0.0.0`` no es una dirección de destino: ni un reproductor
ni la propia GUI pueden ir ahí. Aquí viven las dos conversiones que hacían
falta: la que anuncia una URL navegable y la que usan los clientes para
conectar.
"""

from __future__ import annotations

import socket

LOOPBACK = "127.0.0.1"
#: Escuchar en todos los interfaces. No confundir con un destino: no es
#: navegable, solo vale como ``control_host``.
ANY_IP = "0.0.0.0"
#: Hosts de escucha que no sirven como destino.
_NO_NAVEGABLES = (ANY_IP, "::", "")


def default_route_ip() -> str | None:
    """IPv4 del equipo por la ruta por defecto (la que ve la LAN).

    Es la dirección que alcanza un televisor de la misma red. No abre ninguna
    conexión: un socket UDP hace que el sistema elija la interfaz de salida.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def local_ipv4_addresses() -> list[str]:
    """IPv4 de las interfaces del equipo, sin loopback ni duplicados.

    Es una foto del momento: una VPN o una interfaz virtual aparecen y
    desaparecen, así que la lista solo sirve para ofrecer opciones, nunca para
    validar la que ya está guardada.
    """
    vistas: list[str] = []
    try:
        info = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        info = []
    for entrada in info:
        ip = str(entrada[4][0])
        if not ip.startswith("127.") and ip not in vistas:
            vistas.append(ip)
    return vistas


def connect_host(host: str) -> str:
    """Dirección a la que un cliente de **este equipo** puede conectarse.

    ``0.0.0.0`` es una dirección de escucha: para conectar se usa loopback.
    """
    limpio = (host or "").strip()
    if limpio in ("localhost", "::1"):
        return LOOPBACK
    return LOOPBACK if limpio in _NO_NAVEGABLES else limpio


def announced_host(host: str) -> str:
    """Host que se publica en las URLs para clientes de **fuera**.

    Con ``0.0.0.0`` hay que anunciar la IP con la que el equipo sale a la red:
    un televisor no puede navegar a 0.0.0.0.
    """
    limpio = (host or "").strip()
    if limpio in _NO_NAVEGABLES:
        return default_route_ip() or LOOPBACK
    return limpio


def host_options(current: str | None = None) -> list[tuple[str, str]]:
    """Opciones del desplegable del host: (valor, etiqueta), en orden útil.

    Primero loopback, después la IP con la que el equipo sale a la red (la que
    necesita el reproductor), el resto de interfaces y, al final, escuchar en
    todas. Un valor guardado que ya no exista (DHCP, VPN caída) se conserva
    arriba y se marca: nunca se pierde ni se sustituye por el primero.
    """
    opciones: list[tuple[str, str]] = [
        (LOOPBACK, "127.0.0.1 (solo este equipo)")]
    salida = default_route_ip()
    if salida and salida != LOOPBACK:
        opciones.append((salida, f"{salida} (este equipo en la red)"))
    for ip in local_ipv4_addresses():
        if ip not in {valor for valor, _ in opciones}:
            opciones.append((ip, f"{ip} (otra interfaz)"))
    opciones.append((ANY_IP, "0.0.0.0 (todos los interfaces)"))
    actual = "127.0.0.1" if (current or "").strip() == "localhost" else (
        current or "").strip()
    if actual and actual not in {valor for valor, _ in opciones}:
        opciones.insert(0, (actual, f"{actual} (actual, no disponible)"))
    return opciones
