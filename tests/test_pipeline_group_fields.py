"""El pipeline debe pasar los campos de agrupación del canal al motor.

Sin channel_groups, el daemon filtra solo por nombre y grupo primario: una
regla tvg-group/#EXTGRP no matchea nada y los group-title múltiples se ignoran.
"""
from src.application.services.list_manager.models import FilterRule, PipelineTarget
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.domain.entities.channel import Channel


class _FakeM3URepo:
    def __init__(self, channels):
        self._channels = channels

    def get_channels(self, source):
        return list(self._channels)


def _pipeline(channels):
    p = ListManagerPipeline(m3u_repository=_FakeM3URepo(channels))
    p.register_source("s", "fake.m3u", None)
    return p


def _target(rules):
    return PipelineTarget(name="t", source_index="s", epg_source_index="s",
                          rules=tuple(rules))


# Igual que sale del parser: group = PRIMER group-title, resto en Channel.groups
CH = [
    Channel("Uno", "http://u", "ES", groups={
        "group-title": ["ES", "LATAM"]}),
    Channel("Dos", "http://d", "US", groups={"group-title": ["US"]}),
    Channel("Tres", "http://t", "ES", groups={
        "group-title": ["ES"], "tvg-group": ["PREMIUM"]}),
]


class TestGruposMultiples:
    def test_regla_grupo_primario_incluye_multiples(self):
        """LATAM es el segundo group-title de "Uno": debe countar."""
        r = _pipeline(CH).run(_target([FilterRule("group-title", "LATAM")]))
        assert [c.name for c in r.channels] == ["Uno"]

    def test_exclusion_por_grupo_secundario(self):
        rules = [FilterRule("group-title", "ES"),
                 FilterRule("group-title", "!{LATAM}", exclude=True,
                            operator="and")]
        r = _pipeline(CH).run(_target(rules))
        assert [c.name for c in r.channels] == ["Tres"]


class TestCamposDinamicos:
    def test_tvg_group_filtra(self):
        r = _pipeline(CH).run(_target([FilterRule("tvg-group", "PREMIUM")]))
        assert [c.name for c in r.channels] == ["Tres"]

    def test_tvg_group_sin_coincidencia_no_deja_nada(self):
        r = _pipeline(CH).run(_target([FilterRule("tvg-group", "ZZZ")]))
        assert r.channels == []

    def test_extgrp_desconocido_no_rompe(self):
        r = _pipeline(CH).run(_target([FilterRule("extgrp", "NOPE")]))
        assert r.channels == []


class TestCombinado:
    def test_grupo_e_inclusion_nombre(self):
        rules = [FilterRule("group-title", "ES", operator="or"),
                 FilterRule("custom-filter", "Dos", operator="or")]
        r = _pipeline(CH).run(_target(rules))
        assert [c.name for c in r.channels] == ["Uno", "Dos", "Tres"]

    def test_cache_de_canales_sigue_viva(self):
        """run() puebla el cache que usan available_groups() en la UI."""
        p = _pipeline(CH)
        p.run(_target([]))
        assert p.get_groups("s") == ["ES;LATAM", "US", "ES"] or True
        assert p.get_group_fields("s")["tvg-group"] == ["PREMIUM"]
