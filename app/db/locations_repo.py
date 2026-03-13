import asyncio
import json
import logging
from typing import List, Dict, Any, Optional

from app.db.connection import db

logger = logging.getLogger(__name__)


async def write_locations_to_db(
    locations: List[Dict[str, Any]],
) -> int:
    """
    Insert or update locations in the database.
    """

    if not locations:
        return 0

    loop = asyncio.get_running_loop()

    def _write():

        sql = """
        INSERT INTO locations
        (name, title, storeCode, status, primaryPhone,
         regionCode, administrativeArea, locality,
         postalCode, placeId, labels, fetchedAt)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            title = VALUES(title),
            storeCode = VALUES(storeCode),
            status = VALUES(status),
            primaryPhone = VALUES(primaryPhone),
            regionCode = VALUES(regionCode),
            administrativeArea = VALUES(administrativeArea),
            locality = VALUES(locality),
            postalCode = VALUES(postalCode),
            placeId = VALUES(placeId),
            labels = VALUES(labels),
            fetchedAt = VALUES(fetchedAt),
            updated_at = CURRENT_TIMESTAMP
        """

        count = 0

        for idx, row in enumerate(locations):

            try:
                labels = json.dumps(row.get("labels", []))
            except Exception as e:
                logger.error(
                    f"Failed to serialize labels for row {idx}: {e}"
                )
                labels = "[]"

            try:

                db.execute_update(
                    sql,
                    (
                        row.get("name"),
                        row.get("title"),
                        row.get("storeCode"),
                        row.get("status"),
                        row.get("primaryPhone"),
                        row.get("regionCode"),
                        row.get("administrativeArea"),
                        row.get("locality"),
                        row.get("postalCode"),
                        row.get("placeId"),
                        labels,
                        row.get("fetchedAt"),
                    ),
                )

                count += 1

            except Exception as e:

                logger.error(
                    f"Failed inserting location {row.get('name')}: {e}"
                )

                raise

        return count

    return await loop.run_in_executor(None, _write)


async def delete_location_from_db(
    location_name: str,
) -> bool:
    """
    Delete a location by name.
    """

    loop = asyncio.get_running_loop()

    def _delete():

        sql = "DELETE FROM locations WHERE name = %s"

        db.execute_update(sql, (location_name,))

        logger.info(f"Deleted location {location_name}")

        return True

    return await loop.run_in_executor(None, _delete)


async def read_location_from_db(
    location_name: str,
) -> Optional[Dict[str, Any]]:
    """
    Fetch a single location from the database.
    """

    loop = asyncio.get_running_loop()

    def _read():

        sql = """
        SELECT *
        FROM locations
        WHERE name = %s
        LIMIT 1
        """

        result = db.execute_query(sql, (location_name,))

        if not result:
            return None

        location = result[0]

        if location.get("labels"):

            try:
                location["labels"] = json.loads(location["labels"])
            except Exception:
                location["labels"] = []

        return location

    return await loop.run_in_executor(None, _read)