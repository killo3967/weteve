"""Motor de filtros del gestor de listas.

Semántica equivalente a ``filterThisStream`` de xTeVe (src/m3u.go):
  - Regla de tipo ``group-title``: coincidencia del grupo (con include/exclude).
  - Regla de tipo ``custom-filter``: subcadena sobre grupo o nombre.
  - ``{texto}`` dentro del patrón: incluir adicionalmente los canales que
    cumplan ``texto``.
  - ``!{texto}`` dentro del patrón: excluir los canales que cumplan ``texto``.
"""

import re

from src.application.services.list_manager.models import FilterRule

_REGEXP_YES = re.compile(r"[{]+[^.]+[}]")
_REGEXP_NO = re.compile(r"!+[{]+[^.]+[}]")


def _matches_pattern(haystack: str, needle: str) -> bool:
    return needle in haystack


def evaluate_channel(
    name: str,
    group: str,
    raw_attrs: str,
    rules: list[FilterRule],
) -> bool:
    """Decide si un canal pasa los filtros.

    Args:
        name: nombre del canal.
        group: group-title del canal.
        raw_attrs: atributos crudos del EXTINF (para custom-filter ampliado).
        rules: reglas a evaluar en orden. Un canal se incluye si al menos una
            regla activa lo incluye y ninguna lo excluye (semántica xTeVe:
            la primera regla que decide gana; sin reglas, todo pasa).

    Returns:
        True si el canal pasa los filtros.
    """
    active = [r for r in rules if r.pattern]
    if not active:
        return True

    # Prioridad de exclusión: cualquier !{...} que coincida elimina el canal.
    for rule in active:
        _included, excluded, _base = _split_rule(rule)
        hay_name = name if rule.case_sensitive else name.lower()
        hay_group = group if rule.case_sensitive else group.lower()
        raw = raw_attrs if rule.case_sensitive else raw_attrs.lower()
        exc = excluded if rule.case_sensitive else excluded.lower()

        if excluded and _rule_matches(rule, exc, hay_name, hay_group, raw):
            return False

    for rule in active:
        included, excluded, base = _split_rule(rule)
        hay_name = name if rule.case_sensitive else name.lower()
        hay_group = group if rule.case_sensitive else group.lower()
        raw = raw_attrs if rule.case_sensitive else raw_attrs.lower()
        inc = included if rule.case_sensitive else included.lower()
        base_l = base if rule.case_sensitive else base.lower()
        if included and base:
            # Ej. xTeVe "Sports {deportes}": grupo base O inclusion adicional.
            if (
                _rule_matches(rule, base_l, hay_name, hay_group, raw)
                or _rule_matches(rule, inc, hay_name, hay_group, raw)
            ):
                return True
        elif included:
            if _rule_matches(rule, inc, hay_name, hay_group, raw):
                return True
        elif not base:
            # Patrón solo de exclusión (p. ej. "!{XXX}"): incluye todo lo
            # no excluido.
            return True
        elif _rule_matches(rule, base_l, hay_name, hay_group, raw):
            return True

    return False


def _split_rule(rule: FilterRule) -> tuple[str, str, str]:
    """Extrae (include, exclude, base) del patrón."""
    pattern = rule.pattern
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


def _rule_matches(
    rule: FilterRule,
    pattern: str,
    name_lower: str,
    group_lower: str,
    raw_attrs: str,
) -> bool:
    """Comprueba si el patrón coincide según el campo de la regla."""
    if rule.field == "group-title":
        return _matches_pattern(group_lower, pattern)
    # custom-filter: subcadena sobre grupo, nombre o atributos crudos
    haystack = f"{group_lower} {name_lower} {raw_attrs.lower()}"
    return _matches_pattern(haystack, pattern)
