"""Caché de la EPG parseada: filtrar un canal no re-descarga las guías.

Cambiar una regla reejecuta el pipeline, pero las guías no cambian. Sin caché,
cada clic pagaba descarga + parseo de ~100.000 programas (segundos).
"""


from src.infrastructure.adapters.xmltv_repository import XMLTVRepository

XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="es.la1"><display-name>La 1</display-name></channel>
  <programme start="20261019060000 +0200" stop="20261019070000 +0200"
             channel="es.la1"><title>Noticias</title></programme>
</tv>
"""


def _repo_con_contador(monkeypatch, **kw):
    repo = XMLTVRepository(**kw)
    llamadas = {"parse": 0}
    original = repo._parse_xmltv

    def contar(content):
        llamadas["parse"] += 1
        return original(content)

    monkeypatch.setattr(repo, "_parse_xmltv", contar)
    return repo, llamadas


class TestCacheLocal:
    def test_dos_lecturas_del_mismo_fichero_parsean_una_vez(self, tmp_path,
                                                            monkeypatch):
        guia = tmp_path / "guia.xml"
        guia.write_text(XMLTV, encoding="utf-8")
        repo, llamadas = _repo_con_contador(monkeypatch)
        assert repo.load_epg(str(guia)).channel_names
        assert repo.load_epg(str(guia)).channel_names
        assert llamadas["parse"] == 1

    def test_si_el_fichero_cambia_se_relee(self, tmp_path, monkeypatch):
        guia = tmp_path / "guia.xml"
        guia.write_text(XMLTV, encoding="utf-8")
        repo, llamadas = _repo_con_contador(monkeypatch)
        repo.load_epg(str(guia))
        guia.write_text(XMLTV.replace("Noticias", "Telediario"),
                        encoding="utf-8")
        repo.load_epg(str(guia))
        assert llamadas["parse"] == 2

    def test_un_fichero_que_no_existe_no_se_cachea(self, tmp_path, monkeypatch):
        repo, llamadas = _repo_con_contador(monkeypatch)
        assert repo.load_epg(str(tmp_path / "no.xml")).channel_names == {}
        assert llamadas["parse"] == 0      # no hay contenido que parsear


class TestCacheRemota:
    def _repo(self, monkeypatch, ttl):
        repo, llamadas = _repo_con_contador(monkeypatch, ttl_seconds=ttl)
        monkeypatch.setattr(repo, "_get_content", lambda source: XMLTV.encode())
        return repo, llamadas

    def test_la_url_se_reutiliza_dentro_del_ttl(self, monkeypatch):
        repo, llamadas = self._repo(monkeypatch, ttl=300)
        repo.load_epg("https://ejemplo.net/guia.xml")
        repo.load_epg("https://ejemplo.net/guia.xml")
        assert llamadas["parse"] == 1

    def test_ttl_cero_desactiva_la_cache(self, monkeypatch):
        repo, llamadas = self._repo(monkeypatch, ttl=0)
        repo.load_epg("https://ejemplo.net/guia.xml")
        repo.load_epg("https://ejemplo.net/guia.xml")
        assert llamadas["parse"] == 2

    def test_un_fallo_no_queda_cacheado(self, monkeypatch):
        repo, _ = _repo_con_contador(monkeypatch, ttl_seconds=300)
        intentos = {"n": 0}

        def falla(_source):
            intentos["n"] += 1
            raise OSError("sin red")

        monkeypatch.setattr(repo, "_get_content", falla)
        assert repo.load_epg("https://ejemplo.net/guia.xml").channel_names == {}
        assert repo.load_epg("https://ejemplo.net/guia.xml").channel_names == {}
        assert intentos["n"] == 2       # se reintenta en la siguiente


class TestIconosDelXmltv:
    """Las guías publican el logo en <channel><icon src="…"/>."""

    def test_el_icono_llega_a_channel_info(self, tmp_path):
        from src.infrastructure.adapters.xmltv_repository import XMLTVRepository

        guia = tmp_path / "guia.xml"
        guia.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<tv><channel id="k.la1"><display-name>La 1 HD</display-name>'
            '<icon src="https://logos/la1.png"/></channel>'
            '<channel id="k.a3"><display-name>Antena 3</display-name></channel>'
            '<programme start="20261019120000 +0200" stop="20261019130000 +0200"'
            ' channel="k.la1"><title>P</title></programme></tv>',
            encoding="utf-8")
        epg = XMLTVRepository(ttl_seconds=0).load_epg(str(guia))
        assert epg.channel_info["k.la1"].logo == "https://logos/la1.png"
        assert "k.a3" not in epg.channel_info

    def test_icono_relativo_se_ignora(self, tmp_path):
        from src.infrastructure.adapters.xmltv_repository import XMLTVRepository

        guia = tmp_path / "guia.xml"
        guia.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<tv><channel id="x"><display-name>X</display-name>'
            '<icon src="/img/x.png"/></channel></tv>', encoding="utf-8")
        epg = XMLTVRepository(ttl_seconds=0).load_epg(str(guia))
        assert epg.channel_info == {}
