"""Motor de filtros del gestor de listas.

Semántica equivalente a ``filterThisStream`` de xTeVe (src/m3u.go):
  - Regla de tipo ``group-title``: coincidencia del grupo (con include/exclude).
  - Regla de tipo ``custom-filter``: subcadena sobre el NOMBRE del canal
    (los atributos del EXTINF y la URL no son criterio de selección).
  - Reglas de campos dinámicos (tvg-group, extgrp): subcadena sobre los
    grupos de ese campo; un canal con varios matchea si alguno coincide.
  - Operador por regla: "and" exige cumplir además de las anteriores,
    "or" (default) añade una alternativa.
  - ``{texto}`` dentro del patrón: incluir adicionalmente los canales que
    cumplan ``texto``.
  - ``!{texto}`` dentro del patrón: excluir los canales que cumplan ``texto``.
"""

import re
from functools import lru_cache

from src.application.services.list_manager.models import FilterRule

# Los nombres reales llevan puntos ("C. TORREVISION HD", "A.3", "Antena3.TV"):
# el contenido entre llaves admite cualquier carácter salvo la llave de cierre.
_REGEXP_YES = re.compile(r"[{]+([^}]+)[}]")
_REGEXP_NO = re.compile(r"!+[{]+([^}]+)[}]")


def _matches_pattern(haystack: str, needle: str) -> bool:
    return needle in haystack


def evaluate_channel(
    name: str,
    group: str,
    raw_attrs: str,
    rules: list[FilterRule],
    channel_groups: dict[str, list[str]] | None = None,
) -> bool:
    """Decide si un canal pasa los filtros.

    Args:
        name: nombre del canal.
        group: grupo primario del canal (primer group-title).
        raw_attrs: atributos crudos del EXTINF (reservado; hoy sin uso).
        rules: reglas a evaluar en orden.
        channel_groups: campos de agrupación del canal
            ({"group-title": [...], "tvg-group": [...], "extgrp": [...]}).

    Semántica de combinación (en orden de aparición):
      - Exclusiones primero: cualquier !{...} que coincida elimina el canal.
      - El operador de una regla dice cómo se une a la condición acumulada:
        "and" exige que también esta regla pase; "or" la añade como
        alternativa. Si TODAS las reglas son OR (formato legacy xTeVe) el
        resultado equivale al comportamiento anterior: basta una que pase.

    Returns:
        True si el canal pasa los filtros.
    """
    active = [r for r in rules if r.pattern]
    if not active:
        return True
    groups = channel_groups or {}

    # 1) Exclusiones: prioridad absoluta.
    for rule in active:
        _included, excluded, _base = _split_rule(rule)
        if excluded and _rule_matches(rule, excluded, name, group, groups):
            return False

    # 2) Inclusiones: las reglas "and" son obligatorias; entre las "or"
    #    basta con que una pase. Sin reglas "or", resultan solo las "and".
    def _rule_pass(rule: FilterRule) -> bool:
        included, excluded, base = _split_rule(rule)
        if base.startswith("!"):
            # Exclusión no reconocida (sin llave de cierre, p. ej.): vale como
            # "todo lo no excluido" en vez de como inclusión imposible.
            return True
        if included and base:
            return (
                _rule_matches(rule, base, name, group, groups)
                or _rule_matches(rule, included, name, group, groups)
            )
        if included:
            return _rule_matches(rule, included, name, group, groups)
        if not base or base.startswith("!"):
            # Patrón solo de exclusión (p. ej. "!{XXX}"), incluso si el
            # !{...} no se pudo extraer: incluye todo lo no excluido. Sin
            # esto, una exclusión mal formada actuaría como inclusión y
            # dejaría la lista vacía.
            return True
        return _rule_matches(rule, base, name, group, groups)

    and_rules = [r for r in active if r.operator == "and"]
    or_rules = [r for r in active if r.operator != "and"]
    if not all(_rule_pass(r) for r in and_rules):
        return False
    if or_rules and not any(_rule_pass(r) for r in or_rules):
        return False
    return True

@lru_cache(maxsize=4096)
def _split_pattern(pattern: str) -> tuple[str, str, str]:
    """Extrae (include, exclude, base) del patrón.

    Se cachea por patrón: sin esto se hacían dos búsquedas de regex por regla
    y por canal (37.600 canales × 24 reglas = más de un millón por recarga).
    """
    exclude = ""
    include = ""

    m_no = _REGEXP_NO.search(pattern)
    if m_no:
        exclude = m_no.group(0)[2:-1]
        pattern = pattern.replace(f" {m_no.group(0)}", "").replace(m_no.group(0), "")

    m_yes = _REGEXP_YES.search(pattern)
    if m_yes:
        include = m_yes.group(0)[1:-1]
        pattern = pattern.replace(f" {m_yes.group(0)}", "").replace(m_yes.group(0), "")

    return (include or pattern).strip(), exclude, pattern.strip()


def _split_rule(rule: FilterRule) -> tuple[str, str, str]:
    """(include, exclude, base) del patrón de la regla (cacheado)."""
    return _split_pattern(rule.pattern)


def _rule_matches(
    rule: FilterRule,
    pattern: str,
    name: str,
    group: str,
    channel_groups: dict[str, list[str]],
) -> bool:
    """Comprueba si el patrón coincide según el campo de la regla.

    Insensible a mayúsculas salvo que la regla pida lo contrario.
    """
    if rule.field == "custom-filter":
        hay = name if rule.case_sensitive else name.lower()
        needle = pattern if rule.case_sensitive else pattern.lower()
        return _matches_pattern(hay, needle)

    if rule.field == "group-title":
        # Grupo primario + los múltiples del mismo campo.
        values = [group] + list(channel_groups.get("group-title", []))
    else:
        # Campos dinámicos: tvg-group, extgrp, futuros.
        values = list(channel_groups.get(rule.field, []))

    if not rule.case_sensitive:
        values = [v.lower() for v in values]
        needle = pattern.lower()
    else:
        needle = pattern
    return any(_matches_pattern(v, needle) for v in values)
