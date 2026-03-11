"""Stub service for table registry - PlanetScale uses automatic table creation."""
import logging

logger = logging.getLogger(__name__)


class TableRegistry:
    """Stub table registry for PlanetScale migration."""

    @staticmethod
    async def initialize_all_tables():
        """Stub: Tables are created automatically in PlanetScale on startup."""
        logger.info("Tables are auto-created by PlanetScale connection initialization")
        return True

    @staticmethod
    def get_table(table_name: str):
        """Stub: Return None for compatibility."""
        return None
