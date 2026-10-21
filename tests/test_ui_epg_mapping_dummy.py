"""Tests del desplegable EPG: nombre de canal legible y guía de relleno.

Las tres peticiones del usuario (2026-10-14) sobre la pestaña 'Mapeado EPG'
tienen naturalezas distintas, y esa distinción es lo que estos tests protegen:

  - quitar el sufijo '.TV' y añadir 'Dummy 1h' son **solo visual**: lo que se
    persiste en config.ini y lo que come el emparejador sigue siendo el
    channel-id real del XMLTV;
  - desactivar el canal sin guía es **semántica de salida**: la aplica el
    servidor (tests/test_no_epg_excluded.py) y aquí se comprueba que la UI no
    la contradiga.

Contexto del sufijo: la lista TV.xml usa nombres con el sufijo separado por
punto ('La1.TV', 'Antena3.Internacional.TV'). 'La1TV', sin punto, no es el
patrón y se conserva tal cual.
"""

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt

from src.application.services.list_manager.dummy_epg import (
    BLANK_LABEL,
    DUMMY_LABEL,
)
from src.application.services.list_manager.models import MappedChannel
from src.application.services.list_manager.playlist_builder import build_dummy_epg_id
from src.infrastructure.ui.list_manager_dialog import EpgMappingWidget

LA1_ID = "La1.TV"
A3_ID = "Antena3.Internacional.TV"


def _epg(names: dict[str, str] | None = None) -> SimpleNamespace:
    """Catálogo con el formato real de TV.xml: id y nombre terminan en '.TV'."""
    names = names if names is not None else {LA1_ID: LA1_ID, A3_ID: A3_ID}
    return SimpleNamespace(
        channel_names=names,
        programs_by_channel_ids=lambda: list(names),
    )


def _channels() -> list[MappedChannel]:
    return [
        MappedChannel("La 1 HD", "http://s/la1"),
        MappedChannel("Antena 3 Internacional", "http://s/a3int"),
    ]


@pytest.fixture
def widget(qtbot, tmp_path):
    """Widget de mapeo aislado: no falta la fuente ni el pipeline del diálogo."""
    w = EpgMappingWidget()
    qtbot.addWidget(w)
    w.set_epg(_epg())
    return w


def _texts(w: EpgMappingWidget, row: int = 0) -> list[str]:
    # el combo carga sus opciones al desplegarse: aquí se fuerza
    widget = w.table.cellWidget(row, w.COL_EPG)
    widget.ensure_items()
    combo = widget._combo
    return [combo.itemText(i) for i in range(combo.count())]


def _select(w: EpgMappingWidget, row: int, value: str) -> None:
    """Selecciona una opción y dispara la señal del desplegable.

    `setCurrentText` sobre un combo editable mueve el texto pero no emite
    `activated`: en la app el usuario siempre selecciona de la lista, así que
    hay que emitir a mano para ejercitar el manejador.
    """
    combo = w.table.cellWidget(row, w.COL_EPG)
    combo.set_current(value)
    combo._combo.activated.emit(max(combo._combo.findText(value), 0))


class TestNombresLegibles:
    def test_el_desplegable_muestra_los_nombres_sin_sufijo(self, widget):
        widget.load_unmatched(_channels(), {})
        assert _texts(widget) == ["", DUMMY_LABEL, "Antena3.Internacional", "La1"]

    def test_la_asignacion_persistida_usa_el_id_real(self, widget):
        """Lo que se guarda es el channel-id del XMLTV, no la etiqueta limpia.

        Si se guardara 'La1', el emparejamiento con TV.xml se rompería en
        silencio y la lista quedaría vacía sin ningún error visible.
        """
        widget.load_unmatched(_channels(), {})
        _select(widget, 0, "La1")
        assert widget.get_overrides() == {"http://s/la1": LA1_ID}

    def test_un_override_persistido_con_id_real_se_muestra_limpio(self, widget):
        """config.ini guarda 'La1.TV'; el usuario ve 'La1'."""
        widget.load_unmatched(_channels(), {"http://s/la1": LA1_ID})
        assert widget.table.cellWidget(0, widget.COL_EPG).current_value() == "La1"

    def test_escribir_el_id_con_sufijo_tambien_se_resuelve(self, widget):
        """El combo es editable: pegar el id completo debe seguir funcionando."""
        widget.load_unmatched(_channels(), {})
        _select(widget, 0, LA1_ID)
        assert widget.get_overrides() == {"http://s/la1": LA1_ID}

    def test_un_nombre_sin_sufijo_no_se_toca(self, widget):
        widget._epg_names = {"CanalSinSufijo": "CanalSinSufijo"}
        assert widget.epg_channel_names() == [DUMMY_LABEL, "CanalSinSufijo"]

    def test_un_nombre_con_punto_intermedio_conserva_el_ultimo_tramo(self, widget):
        """'La1.TV' -> 'La1'. El punto central se queda: es parte del nombre."""
        widget._epg_names = {"Canal.Uno.TV": "Canal.Uno.TV"}
        assert widget.epg_channel_names() == [DUMMY_LABEL, "Canal.Uno"]

    def test_el_nombre_del_canal_m3u_no_se_renombra(self, widget):
        """Solo se limpia el catálogo EPG. El nombre del canal es del proveedor
        y no podemos saber si su '.TV' es sufijo o parte del nombre."""
        widget.load_unmatched([MappedChannel("Canal Raro.TV", "http://s/raro")], {})
        assert widget.table.item(0, widget.COL_NAME).text() == "Canal Raro.TV"


class TestDummyPrimero:
    def test_dummy_abre_la_lista_tras_la_opcion_vacia(self, widget):
        widget.load_unmatched(_channels(), {})
        texts = _texts(widget)
        assert texts[0] == ""  # deseleccionar: el canal se queda sin guía
        assert texts[1] == DUMMY_LABEL

    def test_sin_fuente_epg_el_dummy_sigue_disponible(self, widget):
        """Aunque la guía no cargue, la opción de relleno tiene que estar: es
        la única forma de rescatar un canal en ese estado."""
        widget.set_epg(None)
        widget.load_unmatched(_channels(), {})
        assert _texts(widget) == ["", DUMMY_LABEL]

    def test_elegir_dummy_guarda_un_id_propio_del_canal(self, widget):
        """El id dummy se deriva de la identidad del canal. Si se derivara del
        nombre, dos canales homónimos compartirían parrilla."""
        widget.load_unmatched(_channels(), {})
        _select(widget, 1, DUMMY_LABEL)
        assert widget.get_overrides() == {
            "http://s/a3int": build_dummy_epg_id("http://s/a3int")}

    def test_un_id_dummy_persistido_se_muestra_como_dummy(self, widget):
        """Al reabrir, config.ini trae 'weteve-dummy-ab12...' y la casilla debe
        leerse como 'Dummy 1h', no como un id ilegible."""
        dummy = build_dummy_epg_id("http://s/la1")
        widget.load_unmatched(_channels(), {"http://s/la1": dummy})
        assert widget.table.cellWidget(0, widget.COL_EPG).current_value() == DUMMY_LABEL
        assert widget.get_overrides() == {"http://s/la1": dummy}

    def test_la_etiqueta_no_corresponde_a_ningun_id_xmltv(self, widget):
        """'Dummy 1h' no es un canal de la guía: _resolve_epg_id lo devuelve
        intacto y quien lo traduce al id sintético es get_overrides, que es el
        único sitio donde está la url del canal."""
        assert widget._resolve_epg_id(DUMMY_LABEL) == DUMMY_LABEL

    def test_un_canal_real_llamado_dummy_no_secuestra_la_opcion(self, widget):
        widget._epg_names = {"otro.TV": "Dummy"}
        texts = widget.epg_channel_names()
        # 'Dummy 1h' y 'Dummy' son etiquetas distintas: no se pisan.
        assert DUMMY_LABEL in texts and "Dummy" in texts

    def test_un_canal_real_llamado_exactamente_dummy_gana_el_nombre(self, widget):
        """Si la guía trae un canal que se llama literalmente 'Dummy 1h', la
        opción deja de ofrecerse: dos opciones con el mismo texto no son
        distinguibles en la lista, y asignar el canal real vale más que la
        guía sintética."""
        widget.set_epg(_epg({"otro.TV": DUMMY_LABEL}))
        texts = widget.epg_channel_names()
        assert texts.count(DUMMY_LABEL) == 1
        widget.load_unmatched(_channels(), {})
        _select(widget, 0, DUMMY_LABEL)
        assert widget.get_overrides() == {"http://s/la1": "otro.TV"}

    def test_un_id_dummy_ya_persistido_se_reinterpreta_hacia_el_canal_real(
            self, widget):
        """Efecto secundario aceptado de la guarda anterior: si la guía pasa a
        traer un canal llamado 'Dummy 1h', un id sintético guardado antes deja
        de ser dummy y apunta a ese canal. Es preferible a ofrecer dos opciones
        idénticas; el usuario lo ve porque la casilla sigue marcada."""
        dummy_id = build_dummy_epg_id("http://s/la1")
        widget.set_epg(_epg({"otro.TV": DUMMY_LABEL}))
        widget.load_unmatched(_channels(), {"http://s/la1": dummy_id})
        # la etiqueta mostrada sigue siendo 'Dummy 1h' (es un id dummy)...
        assert widget._display_label(dummy_id) == DUMMY_LABEL
        # ...pero al guardar se resuelve contra el catálogo real
        assert widget.get_overrides() == {"http://s/la1": "otro.TV"}

    def test_escribir_dummy_en_minusculas_tambien_es_dummy(self, widget):
        """El desplegable es editable: quien teclee 'dummy 1h' debe obtener la
        guía de relleno, no un id fantasma que ningún reproductor reconoce."""
        widget.load_unmatched(_channels(), {})
        _select(widget, 0, "  dummy 1H ")
        overrides = widget.get_overrides()
        assert list(overrides) == ["http://s/la1"]
        assert list(overrides.values())[0].startswith("weteve-dummy-")

    def test_dummy_en_minusculas_activa_la_fila(self, widget):
        """La casilla debe seguir la misma regla que get_overrides: si el id se
        genera, el canal se sirve y la fila va marcada."""
        widget.load_unmatched(_channels(), {})
        _select(widget, 0, "dummy 1h")
        item = widget.table.item(0, widget.COL_ACTIVE)
        assert item.checkState() == Qt.CheckState.Checked
        assert widget.get_disabled() == ()


class TestCasillaSigueALaAsignacion:
    """Coherencia con la regla de servidor: sin guía no se sirve.

    La casilla 'Activo' arranca desmarcada en una fila sin asignación y se
    marca al asignar guía. No es cosmética: si quedara marcada, al guardar
    volvería a `disabled_channels` y desharía el Dummy 1h recién elegido.
    """

    def test_fila_sin_asignacion_arranca_desactivada(self, widget):
        widget.load_unmatched(_channels(), {})
        assert widget.table.item(0, widget.COL_ACTIVE).checkState() == Qt.CheckState.Unchecked

    def test_fila_con_guia_arranca_activa(self, widget):
        widget.load_unmatched(_channels(), {"http://s/la1": LA1_ID})
        assert widget.table.item(0, widget.COL_ACTIVE).checkState() == Qt.CheckState.Checked
        assert widget.table.item(1, widget.COL_ACTIVE).checkState() == Qt.CheckState.Unchecked

    def test_elegir_dummy_activa_la_fila(self, widget):
        """El rescate tiene que ser de una pulsación: eliges Dummy 1h y el
        canal vuelve a la lista."""
        widget.load_unmatched(_channels(), {})
        _select(widget, 0, DUMMY_LABEL)
        assert widget.table.item(0, widget.COL_ACTIVE).checkState() == Qt.CheckState.Checked
        assert widget.get_disabled() == ()

    def test_quedar_sin_asignacion_desactiva_la_fila(self, widget):
        widget.load_unmatched(_channels(), {"http://s/la1": LA1_ID})
        _select(widget, 0, "")
        assert widget.table.item(0, widget.COL_ACTIVE).checkState() == Qt.CheckState.Unchecked

    def test_desmarcar_a_mano_si_se_registra(self, widget):
        """Apagar un canal que sí tiene guía es una decisión del usuario y va a
        ``disabled_channels``. Sin esto no habría forma de sacar un canal con
        guía de la lista."""
        widget.load_unmatched(_channels(), {"http://s/la1": LA1_ID})
        widget.table.item(0, widget.COL_ACTIVE).setCheckState(Qt.CheckState.Unchecked)
        assert widget.get_disabled() == ("http://s/la1",)

    def test_sin_asignacion_no_contamina_disabled_channels(self, widget):
        """Una fila sin guía no se sirve, pero **no** es un apagado manual.

        ``get_disabled`` alimenta ``disabled_channels`` en config.ini. Si se
        mezclaran, guardar escribiría cientos de urls como 'apagado a mano', y
        el apagado manual se aplica **antes** que la EPG: asignarles Dummy 1h
        después no las rescataría.
        """
        widget.load_unmatched(_channels(), {})
        assert widget.get_disabled() == ()

    def test_apagar_y_reencender_a_mano_no_deja_rastro(self, widget):
        """Lo que el usuario vuelve a marcar sale de ``disabled_channels`` al
        guardar: sin esto la casilla quedaría mintiendo para siempre."""
        widget.load_unmatched(_channels(), {"http://s/la1": LA1_ID},
                              disabled=("http://s/la1",))
        widget.table.item(0, widget.COL_ACTIVE).setCheckState(Qt.CheckState.Checked)
        assert widget.get_disabled() == ()

    def test_un_canal_desactivado_a_mano_con_guia_sigue_desactivado(self, widget):
        widget.load_unmatched(_channels(), {"http://s/la1": LA1_ID},
                              disabled=("http://s/la1",))
        assert widget.table.item(0, widget.COL_ACTIVE).checkState() == Qt.CheckState.Unchecked


class TestSinColisiones:
    def test_dos_canal_clean_no_ambiguos(self, widget):
        """La decisión 'solo etiqueta' se apoyó en que quitar .TV no colisiona.
        Si dos canales del XMLTV quedaran con el mismo nombre limpio, la
        resolución nombre->id debe seguir siendo determinista (gana el primero
        del catálogo, que es el orden en que se listan)."""
        widget._epg_names = {"uno.TV": "Iguales", "dos.TV": "Iguales"}
        assert widget._resolve_epg_id("Iguales") == "uno.TV"

    def test_el_blank_no_es_una_id(self, widget):
        assert widget._resolve_epg_id(BLANK_LABEL) == ""
