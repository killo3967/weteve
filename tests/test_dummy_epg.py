"""Tests de la guía de relleno (Dummy 1h) del gestor de listas."""

from datetime import datetime

import pytest

from src.application.services.list_manager.dummy_epg import (
    DUMMY_HOURS,
    DUMMY_LABEL,
    build_dummy_channel,
    is_dummy_id,
    is_dummy_label,
    make_dummy_id,
)

class TestIdentificacion:
    def test_la_etiqueta_es_dummy_1h(self):
        assert DUMMY_LABEL == "Dummy 1h"

    def test_is_dummy_label_reconoce_la_etiqueta(self):
        assert is_dummy_label(DUMMY_LABEL) is True

    def test_is_dummy_label_tolera_espacios_y_mayusculas(self):
        assert is_dummy_label("  dummy 1H ") is True

    def test_is_dummy_label_no_confunde_un_canal_real(self):
        assert is_dummy_label("La1.TV") is False
        assert is_dummy_label("") is False

    def test_el_prefijo_del_id_delata_un_dummy(self):
        cid = make_dummy_id("http://s/la1", "LA 1 HD")
        assert is_dummy_id(cid) is True

    def test_un_id_real_no_es_dummy(self):
        assert is_dummy_id("La1.TV") is False
        assert is_dummy_id(None) is False


class TestIdPorCanal:
    def test_dos_canales_no_comparten_id(self):
        a = make_dummy_id("http://s/la1", "LA 1 HD")
        b = make_dummy_id("http://s/la2", "LA 2 HD")
        assert a != b

    def test_el_mismo_canal_es_estable_entre_regeneraciones(self):
        """El id se deriva de la url: si no cambia la url, no cambia el id.

        Importa porque el demonio regenera el epg.xml en cada recarga y el
        reproductor cachea la guía: ids inestables romperían el emparejamiento.
        """
        first = make_dummy_id("http://s/la1", "LA 1 HD")
        second = make_dummy_id("http://s/la1", "Otro nombre")
        assert first == second

    def test_sin_url_se_usa_el_nombre_como_clave(self):
        assert make_dummy_id("", "LA 1 HD") == make_dummy_id("", "LA 1 HD")
        assert make_dummy_id("", "LA 1 HD") != make_dummy_id("", "LA 2 HD")

    def test_el_id_es_seguro_como_channel_id_xmltv(self):
        cid = make_dummy_id("http://s/la1?token=á&x=1", "LA 1")
        assert cid.startswith("weteve-dummy-")
        assert not any(c in cid for c in ' &?="\'<>')


class TestParrilla:
    def test_cubre_siete_dias_en_bloques_de_una_hora(self):
        assert DUMMY_HOURS == 168
        programs = build_dummy_channel("LA 1 HD", "weteve-dummy-x",
                                        now=datetime(2026, 10, 14, 20, 37))
        assert len(programs) == DUMMY_HOURS

    def test_los_bloques_empiezan_en_hora_en_punto(self):
        programs = build_dummy_channel("LA 1 HD", "weteve-dummy-x",
                                       now=datetime(2026, 10, 14, 20, 37))
        assert programs[0].start_time == datetime(2026, 10, 14, 20, 0)
        assert programs[0].end_time == datetime(2026, 10, 14, 21, 0)

    def test_los_bloques_son_contiguos_sin_huecos(self):
        programs = build_dummy_channel("LA 1 HD", "weteve-dummy-x",
                                       now=datetime(2026, 10, 14, 20, 0))
        for prev, nxt in zip(programs, programs[1:]):
            assert prev.end_time == nxt.start_time

    def test_el_titulo_es_el_nombre_del_canal(self):
        """Cada canal tiene id propio, así que el título puede ser el suyo.

        Es lo que hace útil el dummy: el reproductor muestra 'LA 1 HD' en la
        franja en vez de 'sin información'.
        """
        programs = build_dummy_channel("LA 1 HD", "weteve-dummy-x",
                                       now=datetime(2026, 10, 14, 20, 0))
        assert {p.title for p in programs} == {"LA 1 HD"}

    def test_cada_programa_apunta_al_id_dummy(self):
        programs = build_dummy_channel("LA 1 HD", "weteve-dummy-x",
                                       now=datetime(2026, 10, 14, 20, 0))
        assert {p.channel_id for p in programs} == {"weteve-dummy-x"}

    def test_ancla_desde_la_ultima_hora_completada(self):
        """El bloque en curso debe cubrir el 'ahora': si se ancla a la hora
        siguiente, el reproductor no muestra nada durante el resto de la franja.
        """
        programs = build_dummy_channel("X", "weteve-dummy-x",
                                       now=datetime(2026, 10, 14, 20, 59))
        first = programs[0]
        assert first.start_time <= datetime(2026, 10, 14, 20, 59) < first.end_time
