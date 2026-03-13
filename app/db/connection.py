import logging
import time
from threading import Lock
from typing import List, Dict, Optional

import certifi
import pymysql
from pymysql.cursors import DictCursor

from app.config import settings

logger = logging.getLogger(__name__)


class PlanetScaleDB:
    """
    PlanetScale MySQL connection manager with retry logic and
    safe lifecycle handling.
    """

    def __init__(self):
        self._connection = None
        self._connection_time = None
        self._lock = Lock()

        self.config = settings.PLANETSCALE_CONNECTION_CONFIG

        self.connection_timeout = 30
        self.max_connection_age = 3600

    # ---------------------------------------------------------
    # Connection Health
    # ---------------------------------------------------------

    def _is_connection_alive(self) -> bool:
        if self._connection is None:
            return False

        try:
            if not self._connection.open:
                return False

            self._connection.ping(reconnect=False)

            if self._connection_time:
                age = time.time() - self._connection_time
                if age > self.max_connection_age:
                    logger.info(
                        f"DB connection expired ({age:.0f}s), reconnecting..."
                    )
                    return False

            return True

        except Exception as e:
            logger.debug(f"Connection health check failed: {e}")
            return False

    # ---------------------------------------------------------
    # Connection Getter
    # ---------------------------------------------------------

    def _get_connection(self):
        if self._is_connection_alive():
            return self._connection

        with self._lock:

            if self._is_connection_alive():
                return self._connection

            if self._connection:
                try:
                    self._connection.close()
                except Exception:
                    pass

            try:
                self._connection = pymysql.connect(
                    host=self.config["host"],
                    user=self.config["user"],
                    password=self.config["password"],
                    database=self.config["database"],
                    charset="utf8mb4",
                    cursorclass=DictCursor,
                    autocommit=False,
                    connect_timeout=self.connection_timeout,
                    read_timeout=self.connection_timeout,
                    write_timeout=self.connection_timeout,
                    ssl={"ca": certifi.where()},
                )

                self._connection_time = time.time()

                logger.info("Connected to PlanetScale")

            except Exception as e:
                logger.error(f"Failed to connect to PlanetScale: {e}")
                self._connection = None
                raise

        return self._connection

    # ---------------------------------------------------------
    # Query Execution
    # ---------------------------------------------------------

    def execute_query(
        self,
        sql: str,
        params: Optional[tuple] = None,
        retries: int = 2,
    ) -> List[Dict]:

        last_error = None

        for attempt in range(retries + 1):

            try:

                conn = self._get_connection()

                with conn.cursor() as cursor:
                    cursor.execute(sql, params or ())
                    return cursor.fetchall()

            except (
                pymysql.err.OperationalError,
                pymysql.err.DatabaseError,
                AttributeError,
            ) as e:

                last_error = e

                if attempt < retries:

                    self._connection = None

                    backoff = 0.5 * (2**attempt)

                    logger.warning(
                        f"DB query retry {attempt+1}/{retries+1} "
                        f"after error: {type(e).__name__}"
                    )

                    time.sleep(backoff)

                else:
                    logger.error(f"Query failed after retries: {e}")

            except Exception as e:
                logger.error(f"Unexpected DB query error: {e}")
                raise

        raise last_error

    # ---------------------------------------------------------
    # Update Execution
    # ---------------------------------------------------------

    def execute_update(
        self,
        sql: str,
        params: Optional[tuple] = None,
        retries: int = 2,
    ) -> int:

        last_error = None

        for attempt in range(retries + 1):

            conn = None

            try:

                conn = self._get_connection()

                with conn.cursor() as cursor:

                    cursor.execute(sql, params or ())

                    conn.commit()

                    return cursor.rowcount

            except (
                pymysql.err.OperationalError,
                pymysql.err.DatabaseError,
                AttributeError,
            ) as e:

                last_error = e

                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                if attempt < retries:

                    self._connection = None

                    backoff = 0.5 * (2**attempt)

                    logger.warning(
                        f"DB update retry {attempt+1}/{retries+1}"
                    )

                    time.sleep(backoff)

                else:
                    logger.error(f"Update failed after retries: {e}")

            except Exception as e:

                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                logger.error(f"Unexpected DB update error: {e}")
                raise

        raise last_error

    # ---------------------------------------------------------
    # Batch Insert
    # ---------------------------------------------------------

    def execute_batch_insert(
        self,
        table: str,
        columns: List[str],
        rows: List[tuple],
        retries: int = 2,
    ) -> int:

        if not rows:
            return 0

        last_error = None

        column_names = ", ".join([f"`{col}`" for col in columns])
        placeholders = ", ".join(["%s"] * len(columns))

        sql = f"""
        INSERT INTO `{table}`
        ({column_names})
        VALUES ({placeholders})
        """

        for attempt in range(retries + 1):

            conn = None

            try:

                conn = self._get_connection()

                with conn.cursor() as cursor:

                    cursor.executemany(sql, rows)

                    conn.commit()

                    return cursor.rowcount

            except Exception as e:

                last_error = e

                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                if attempt < retries:

                    self._connection = None

                    backoff = 0.5 * (2**attempt)

                    logger.warning(
                        f"Batch insert retry {attempt+1}/{retries+1}"
                    )

                    time.sleep(backoff)

                else:
                    logger.error(f"Batch insert failed: {e}")

        raise last_error

    # ---------------------------------------------------------
    # Close
    # ---------------------------------------------------------

    def close(self):

        if self._connection:

            try:
                self._connection.close()
                logger.info("PlanetScale connection closed")

            except Exception as e:
                logger.error(f"Error closing connection: {e}")

            finally:
                self._connection = None


# Singleton instance
db = PlanetScaleDB()