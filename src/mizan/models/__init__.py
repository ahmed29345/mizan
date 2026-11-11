"""Import every model here: Alembic only sees models that have been imported."""

from mizan.models.base import Base
from mizan.models.regulation import Article, Authority, Language, Regulation, RegulationStatus

__all__ = ["Article", "Authority", "Base", "Language", "Regulation", "RegulationStatus"]
