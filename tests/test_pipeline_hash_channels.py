"""Los canales con nombre que empieza por # se descartan siempre.

En muchas listas IPTV los separadores de sección ("### SUP MOVIES ###",
"#### NEWS ####") se codifican como canales reales con nombre empezando por
#. No son canales: el usuario quiere que se filtren automaticamente, sin
necesidad de una regla explicita. El parser si descarta las LINEAS # (son
directivas M3U), pero no el nombre del canal.
"""

from src.application.services.list_manager.models import FilterRule, PipelineTarget
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.domain.entities.channel import Channel


class _FakeM3URepo:
    def __init__(self, channels):
        self._channels = channels

    def get_channels(self, source):
        return list(self._channels)


class _FakeEpgRepo:
    def load_epg(self, source, fresh=False):
        from src.domain.entities.epg import EPGData
        return EPGData()


def _pipeline(channels):
    p = ListManagerPipeline(m3u_repository=_FakeM3URepo(channels),
                            epg_repository=_FakeEpgRepo())
    p.register_source(0, "/x.m3u", None)
    return p


def _target(rules=()):
    return PipelineTarget(name="t", source_index=0, rules=tuple(rules))


class TestHashChannelsFiltered:
    def test_canal_con_nombre_hash_se_descarta_sin_reglas(self):
        chans = [
            Channel("La 1 HD", "http://s/la1", "Spain"),
            Channel("### SUP MOVIES ###", "http://s/sep", "Spain"),
            Channel("#### NEWS ####", "http://s/sep2", "Arabian"),
            Channel("#DAZN 1 FULL HD", "http://s/dazn", "Spain"),
        ]
        p = _pipeline(chans)
        result = p.run(_target())
        nombres = [c.name for c in result.channels]
        assert nombres == ["La 1 HD"]

    def test_total_input_sigue_contando_todos(self):
        """El filtro es implicito, no cambia lo que habia en la fuente."""
        chans = [
            Channel("La 1 HD", "http://s/la1", "Spain"),
            Channel("### SEP ###", "http://s/sep", "Spain"),
        ]
        p = _pipeline(chans)
        result = p.run(_target())
        assert result.total_input == 2
        assert result.filtered_count == 1

    def test_name_con_hash_intermedia_no_se_descarta(self):
        """Solo el prefijo # es separador; 'Tele Mundo #1' es nombre legitimo."""
        chans = [
            Channel("Tele Mundo #1", "http://s/tm", "Spain"),
            Channel("Canal Normal", "http://s/n", "Spain"),
        ]
        p = _pipeline(chans)
        result = p.run(_target())
        assert [c.name for c in result.channels] == ["Tele Mundo #1", "Canal Normal"]

    def test_se_aplica_aun_con_reglas_de_inclusion(self):
        """Aunque una regla lo incluya, el separador # no pasa."""
        chans = [
            Channel("### MOVIES ###", "http://s/sep", "Movies"),
            Channel("HBO", "http://s/hbo", "Movies"),
        ]
        p = _pipeline(chans)
        rule = FilterRule.parse("group-title:Movies:0")
        result = p.run(_target(rules=[rule]))
        assert [c.name for c in result.channels] == ["HBO"]

    def test_no_desestabiliza_el_contador_de_sin_epg(self):
        chans = [
            Channel("La 1 HD", "http://s/la1", "Spain"),
            Channel("### SEP ###", "http://s/sep", "Spain"),
        ]
        p = _pipeline(chans)
        result = p.run(_target())
        # el separador no debe aparecer tampoco en los no casados con EPG
        assert not any(u.lstrip().startswith("#") for u in result.unmatched)
