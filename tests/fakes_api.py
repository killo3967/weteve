"""Doble del ``ApiClient`` para los tests de la GUI.

La GUI es un frontend: todo lo que sabe del motor lo pregunta por la API. Este
fake permite fijar esas respuestas sin daemon y sin sockets.
"""


class FakeApi:
    """Cliente falso con respuestas configurables por atributo."""

    root_url = "http://127.0.0.1:34400"

    def __init__(self, down: bool = False) -> None:
        self.down = down
        self.served = 3
        self.served_channels: list[dict] = []
        self.epg: list[dict] = []
        self.groups_list = ["CINE", "DEPORTES", "INFANTIL"]
        self.fields: dict = {"group-title": ["CINE"]}
        self.preview_names: list[str] = []
        self.preview_total = 0
        self.running = True
        self.calls: list[tuple] = []

    # -- lecturas ------------------------------------------------------------

    def target_status(self, target_id: str):
        if self.down:
            return 0, {"error": "sin conexión"}
        return 200, {"name": "test", "running": self.running,
                     "port": 9999, "channel_count": self.served}

    def mapping_candidates(self, target_id: str, limit: int = 1000):
        if self.down:
            return 0, {"error": "sin conexión"}
        rows = list(self.served_channels)
        return 200, {"channels": rows, "total": len(rows),
                     "count": len(rows), "offset": 0}

    def epg_channels(self, target_id: str, limit: int = 1000):
        if self.down:
            return 0, {"error": "sin conexión"}
        rows = list(self.epg)
        return 200, {"channels": rows, "total": len(rows),
                     "count": len(rows), "offset": 0}

    def groups(self, target_id: str):
        if self.down:
            return 0, {"error": "sin conexión"}
        return 200, {"groups": list(self.groups_list)}

    def group_fields(self, target_id: str):
        if self.down:
            return 0, {"error": "sin conexión"}
        return 200, {"fields": dict(self.fields)}

    def preview(self, target_id: str, rules: list[str]):
        self.calls.append(("preview", list(rules)))
        if self.down:
            return 0, {"error": "sin conexión"}
        return 200, {"channels": list(self.preview_names),
                     "count": len(self.preview_names),
                     "total": self.preview_total}

    def status(self):
        """Estado agregado: refleja ``down`` y el target 0 (la pestaña Estado)."""
        if self.down:
            return 0, {"error": "sin conexión"}
        url = "http://127.0.0.1:34400/weteve/0"
        return 200, {"targets": {"0": {
            "name": "test",
            "running": self.running,
            "port": 34400,
            "channel_count": self.served if self.running else None,
            "playlist_url": f"{url}/playlist.m3u" if self.running else None,
            "epg_url": f"{url}/epg.xml" if self.running else None,
            "error": None,
        }}}

    # -- escrituras ----------------------------------------------------------

    def put(self, path: str, payload: dict):
        self.calls.append(("put", path, payload))
        return 200, {"ok": True}

    def set_sources(self, sources: list[dict]):
        self.calls.append(("sources", list(sources)))
        if self.down:
            return 0, {"error": "sin conexión"}
        return 200, {"ok": True, "sources": list(sources), "errors": []}

    def set_target_sources(self, target_id: str, m3u_ids: list[str],
                           epg_ids: list[str], reload: bool = True):
        self.calls.append(("target_sources", target_id, list(m3u_ids),
                           list(epg_ids), reload))
        return 200, {"ok": True}

    def sources(self):
        return 200, {"sources": []}

    def set_settings(self, target_id: str, **changes):
        self.calls.append(("settings", target_id, changes))
        return 200, {"ok": True}

    def service(self, target_id: str, action: str):
        self.calls.append(("service", target_id, action))
        self.running = action == "start"
        return 200, {"ok": True,
                     "status": {"running": self.running, "port": 9999}}

    def run(self, target_id: str):
        self.calls.append(("run", target_id))
        return 200, {"filtered_count": self.served}
