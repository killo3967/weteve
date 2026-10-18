"""Pruebas de la cartera global de fuentes (SourceCatalog)."""


from src.application.services.list_manager.source_catalog import (
    SourceCatalog,
    SourceEntry,
    new_source_id,
)


def _entry(**kw):
    base = dict(id="a1", name="platero", kind="m3u", source_type="file",
                location="C:/x.m3u", refresh_min=60, enabled=True)
    base.update(kw)
    return SourceEntry(**base)


class TestSourceEntry:
    def test_con_los_campos_minimos_quedan_por_defecto(self):
        e = SourceEntry(id="x", name="n", kind="epg", source_type="url",
                        location="http://y")
        assert e.refresh_min == 0
        assert e.enabled is True

    def test_with_updates_no_mut_original(self):
        e = _entry()
        renamed = e.with_updates(name="otro")
        assert e.name == "platero" and renamed.name == "otro"
        assert renamed.id == e.id

    def test_new_source_id_es_unico_y_estable_en_formato(self):
        ids = {new_source_id() for _ in range(200)}
        assert len(ids) == 200
        assert all(len(i) == 12 and i.isalnum() for i in ids)


class TestSourceCatalogLectura:
    def test_all_preserva_el_orden_insercion(self):
        c = SourceCatalog([_entry(id="a"), _entry(id="b"), _entry(id="c")])
        assert [e.id for e in c.all()] == ["a", "b", "c"]
        assert len(c) == 3

    def test_get_devuelve_none_para_id_inexistente(self):
        assert SourceCatalog([_entry(id="a")]).get("zz") is None

    def test_of_kind_filtra_por_clase(self):
        c = SourceCatalog([_entry(id="a", kind="m3u"),
                           _entry(id="b", kind="epg"),
                           _entry(id="c", kind="m3u")])
        assert [e.id for e in c.of_kind("m3u")] == ["a", "c"]
        assert [e.id for e in c.of_kind("epg")] == ["b"]

    def test_of_kind_enabled_solo_descarta_desactivadas(self):
        c = SourceCatalog([_entry(id="a"), _entry(id="b", enabled=False)])
        assert [e.id for e in c.of_kind("m3u", enabled_only=True)] == ["a"]


class TestSourceCatalogMutacion:
    def test_add_nuevo_reemplaza_por_id(self):
        c = SourceCatalog()
        c.add(_entry(id="a", name="uno"))
        c.add(_entry(id="a", name="dos"))
        assert len(c) == 1
        assert c.get("a").name == "dos"

    def test_add_new_genera_id_y_devuelve_la_entrada(self):
        c = SourceCatalog()
        e = c.add_new("guias", "epg", "url", "http://e.xml", refresh_min=30)
        assert e.kind == "epg" and e.source_type == "url"
        assert c.get(e.id) is e

    def test_remove_indica_si_exista(self):
        c = SourceCatalog([_entry(id="a")])
        assert c.remove("a") is True
        assert c.remove("a") is False
        assert len(c) == 0

    def test_update_ignora_campos_desconocidos_y_el_id(self):
        c = SourceCatalog([_entry(id="a")])
        up = c.update("a", name="nuevo", id="otro", basura=1)
        assert up.name == "nuevo"
        assert up.id == "a"
        assert not hasattr(up, "basura")

    def test_update_sobre_id_inexistente_devuelve_none(self):
        assert SourceCatalog().update("nope", name="x") is None


class TestMinRefresh:
    def test_min_periodo_positivo_de_las_ids(self):
        c = SourceCatalog([_entry(id="a", refresh_min=60),
                           _entry(id="b", refresh_min=10),
                           _entry(id="c", refresh_min=30)])
        assert c.min_refresh_min(["a", "c"]) == 30
        assert c.min_refresh_min(["a", "b", "c"]) == 10

    def test_estaticas_refresh_0_se_ignoran(self):
        c = SourceCatalog([_entry(id="a", refresh_min=0),
                           _entry(id="b", refresh_min=45)])
        assert c.min_refresh_min(["a", "b"]) == 45

    def test_todas_estaticas_devuelve_0(self):
        c = SourceCatalog([_entry(id="a", refresh_min=0)])
        assert c.min_refresh_min(["a"]) == 0

    def test_desactivadas_no_cuentan(self):
        c = SourceCatalog([_entry(id="a", refresh_min=5, enabled=False),
                           _entry(id="b", refresh_min=50)])
        assert c.min_refresh_min(["a", "b"]) == 50

    def test_ids_de_desconocidos_o_lista_vacia_devuelve_0(self):
        c = SourceCatalog([_entry(id="a", refresh_min=10)])
        assert c.min_refresh_min(["fantasma"]) == 0
        assert c.min_refresh_min([]) == 0
