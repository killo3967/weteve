"""Tests de LogoCacheService (sin red: índice, prune, extensión)."""

import time
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from src.infrastructure.ui.logo_cache_service import (  # noqa: E402
    LogoCacheService,
    _ext_for,
)


def test_ext_for_signatures():
    assert _ext_for(b"\x89PNG\r\n\x1a\n" + b"x") == ".png"
    assert _ext_for(b"\xff\xd8\xff\xe0") == ".jpg"
    assert _ext_for(b"GIF89a....") == ".gif"
    assert _ext_for(b"RIFF1234WEBP") == ".webp"
    assert _ext_for(b"whatever") == ".img"


def _svc(tmp: Path) -> LogoCacheService:
    return LogoCacheService(tmp / "logos")


def test_index_persisted(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    # Simular entrada descargada sin red: escribir fichero + índice.
    target = svc._dir / "abc.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    svc._index["http://x/logo"] = {"file": "abc.png",
                                   "fetched_at": time.time()}
    svc._save_index()
    # Un servicio nuevo sirve de caché (emite logo_ready inmediatamente).
    svc2 = _svc(tmp_path)
    got: list[tuple[str, str]] = []
    svc2.logo_ready.connect(lambda u, p: got.append((u, p)))
    svc2.request("http://x/logo")
    assert got == [("http://x/logo", str(target))]


def test_ttl_expiry(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    target = svc._dir / "old.png"
    target.write_bytes(b"x")
    svc._index["http://x/old"] = {"file": "old.png",
                                  "fetched_at": time.time() - 8 * 24 * 3600}
    svc._save_index()
    svc2 = _svc(tmp_path)
    got: list[str] = []
    svc2.logo_ready.connect(lambda u, p: got.append(u))
    # Fresco en disco pero caducado: NO sirve de caché (pediría red; con URL
    # inalcanzable el worker falla en silencio y no emite).
    svc2.request("http://x/old")
    assert got == []


def test_prune_removes_orphans_and_expired(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    keep = svc._dir / "keep.png"
    keep.write_bytes(b"x")
    dead = svc._dir / "dead.png"
    dead.write_bytes(b"x")
    orphan = svc._dir / "orphan.img"
    orphan.write_bytes(b"x")
    svc._index["http://x/keep"] = {"file": "keep.png",
                                   "fetched_at": time.time()}
    svc._index["http://x/dead"] = {"file": "dead.png",
                                   "fetched_at": time.time() - 9 * 24 * 3600}
    svc.prune()
    assert keep.exists()
    assert not dead.exists()
    assert not orphan.exists()
    assert list(svc._index) == ["http://x/keep"]


def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.shutdown()
    svc.shutdown()  # no raises
    svc.request("http://x/y")  # tras cierre: no-op, no excepción
