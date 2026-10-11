"""Gestor de listas: pipeline M3U -> filtros -> EPG -> mini-web.

Especificación: odd/tasks/list-manager.md
"""

from src.application.services.list_manager.models import (
    FilterRule,
    PipelineTarget,
    ServiceConfig,
)

__all__ = ["FilterRule", "PipelineTarget", "ServiceConfig"]
