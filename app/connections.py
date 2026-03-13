# import asyncio
# import logging
# import json
# import time
# from datetime import datetime
# from typing import Any, Dict, List, Optional
# from threading import Lock

# import certifi
# import pymysql
# from pymysql.cursors import DictCursor
# from google.auth.transport.requests import Request as GoogleRequest
# from google.oauth2.credentials import Credentials

# from app.config import settings

# __all__ = [
#     "db",
#     "init_db",
#     "save_google_token",
#     "save_google_token_blocking",
#     "get_google_credentials",
#     "append_reviews_to_db",
#     "append_lifetime_reviews",
#     "upsert_rating_summary",
#     "write_locations_to_db",
#     "delete_location_from_db",
#     "read_location_from_db",
#     "bulk_upsert_review_analytics",
#     "get_location_analytics",
#     "get_batch_analytics",
#     "close_db",
# ]

# logger = logging.getLogger(__name__)


# class PlanetScaleDB:
#     def __init__(self):
#         self._connection = None
#         self._connection_time = None
#         self._lock = Lock()
#         self.config = settings.PLANETSCALE_CONNECTION_CONFIG
#         self.connection_timeout = 30
#         self.max_connection_age = 3600

#     def _is_connection_alive(self):
#         """Check if the connection is alive and safe to use."""
#         if self._connection is None:
#             return False
        
#         try:
#             if not self._connection.open:
#                 return False
            
#             self._connection.ping(reconnect=False)
            
#             if self._connection_time:
#                 age = time.time() - self._connection_time
#                 if age > self.max_connection_age:
#                     logger.info(f"Connection age ({age:.0f}s) exceeds max age, reconnecting")
#                     return False
            
#             return True
#         except Exception as e:
#             logger.debug(f"Connection health check failed: {type(e).__name__}: {e}")
#             return False

#     def _get_connection(self):
#         """Get or create a database connection with proper lifecycle management."""
#         if self._is_connection_alive():
#             return self._connection
        
#         with self._lock:
#             if self._is_connection_alive():
#                 return self._connection
            
#             if self._connection is not None:
#                 try:
#                     self._connection.close()
#                 except Exception:
#                     pass
#                 self._connection = None
            
#             try:
#                 self._connection = pymysql.connect(
#                     host=self.config["host"],
#                     user=self.config["user"],
#                     password=self.config["password"],
#                     database=self.config["database"],
#                     charset="utf8mb4",
#                     cursorclass=DictCursor,
#                     autocommit=False,
#                     # ssl_verify_cert=True,
#                     # ssl_verify_identity=True,
#                     connect_timeout=self.connection_timeout,
#                     read_timeout=self.connection_timeout,
#                     write_timeout=self.connection_timeout,
#                     ssl={
#                         "ca":certifi.where(),
#                     }
#                 )
#                 self._connection_time = time.time()
#                 logger.debug("Connected to PlanetScale database")
#             except Exception as e:
#                 logger.error(f"Failed to connect to PlanetScale: {e}")
#                 self._connection = None
#                 raise
        
#         return self._connection

#     def close(self):
#         """Close the database connection."""
#         if self._connection:
#             try:
#                 self._connection.close()
#                 logger.info("Closed PlanetScale database connection")
#             except Exception as e:
#                 logger.error(f"Error closing connection: {e}")
#             finally:
#                 self._connection = None

#     def execute_query(self, sql: str, params: tuple = None, retries: int = 2) -> List[Dict]:
#         """Execute a SELECT query with retry logic."""
#         last_error = None
        
#         for attempt in range(retries + 1):
#             try:
#                 conn = self._get_connection()
#                 with conn.cursor() as cursor:
#                     cursor.execute(sql, params or ())
#                     return cursor.fetchall()
#             except (pymysql.err.OperationalError, pymysql.err.DatabaseError, AttributeError) as e:
#                 last_error = e
#                 error_msg = str(e).lower()
                
#                 if attempt < retries:
#                     self._connection = None
#                     backoff = 0.5 * (2 ** attempt)
#                     logger.warning(f"Database error (attempt {attempt + 1}/{retries + 1}), retrying in {backoff:.1f}s: {type(e).__name__}")
#                     time.sleep(backoff)
#                 else:
#                     logger.error(f"Query failed after {retries + 1} attempts: {e}")
#             except Exception as e:
#                 logger.error(f"Query execution error: {e}")
#                 raise
        
#         raise last_error if last_error else RuntimeError("Query execution failed")

#     def execute_update(self, sql: str, params: tuple = None, retries: int = 2) -> int:
#         """Execute an INSERT/UPDATE/DELETE query with retry logic."""
#         last_error = None
        
#         for attempt in range(retries + 1):
#             conn = None
#             try:
#                 conn = self._get_connection()
#                 with conn.cursor() as cursor:
#                     cursor.execute(sql, params or ())
#                     conn.commit()
#                     return cursor.rowcount
#             except (pymysql.err.OperationalError, pymysql.err.DatabaseError, AttributeError) as e:
#                 last_error = e
#                 is_duplicate_column = "Duplicate column name" in str(e) or "1060" in str(e)
                
#                 if conn:
#                     try:
#                         conn.rollback()
#                     except Exception:
#                         pass
                
#                 if attempt < retries:
#                     self._connection = None
#                     backoff = 0.5 * (2 ** attempt)
#                     if not is_duplicate_column:
#                         logger.warning(f"Database error (attempt {attempt + 1}/{retries + 1}), retrying in {backoff:.1f}s: {type(e).__name__}")
#                     time.sleep(backoff)
#                 else:
#                     if not is_duplicate_column:
#                         logger.error(f"Update failed after {retries + 1} attempts: {e}")
#             except Exception as e:
#                 is_duplicate_column = "Duplicate column name" in str(e) or "1060" in str(e)
#                 if conn:
#                     try:
#                         conn.rollback()
#                     except Exception:
#                         pass
#                 if not is_duplicate_column:
#                     logger.error(f"Update execution error: {e}")
#                 raise
        
#         raise last_error if last_error else RuntimeError("Update execution failed")

#     def execute_batch_insert(self, table: str, columns: List[str], rows: List[tuple], retries: int = 2) -> int:
#         """Batch insert multiple rows efficiently with retry logic."""
#         if not rows:
#             return 0
        
#         last_error = None
        
#         for attempt in range(retries + 1):
#             conn = None
#             try:
#                 conn = self._get_connection()
#                 column_names = ", ".join([f"`{col}`" for col in columns])
#                 placeholders = ", ".join(["%s"] * len(columns))
#                 sql = f"INSERT INTO `{table}` ({column_names}) VALUES ({placeholders})"
                
#                 with conn.cursor() as cursor:
#                     cursor.executemany(sql, rows)
#                     conn.commit()
#                     return cursor.rowcount
#             except (pymysql.err.OperationalError, pymysql.err.DatabaseError, AttributeError) as e:
#                 last_error = e
#                 if conn:
#                     try:
#                         conn.rollback()
#                     except Exception:
#                         pass
                
#                 if attempt < retries:
#                     self._connection = None
#                     backoff = 0.5 * (2 ** attempt)
#                     logger.warning(f"Batch insert error (attempt {attempt + 1}/{retries + 1}), retrying in {backoff:.1f}s: {type(e).__name__}")
#                     time.sleep(backoff)
#                 else:
#                     logger.error(f"Batch insert failed after {retries + 1} attempts: {e}")
#             except Exception as e:
#                 if conn:
#                     try:
#                         conn.rollback()
#                     except Exception:
#                         pass
#                 logger.error(f"Batch insert execution error: {e}")
#                 raise
        
#         raise last_error if last_error else RuntimeError("Batch insert failed")

#     def execute_batch_upsert(self, table: str, columns: List[str], rows: List[tuple], unique_key: str = "reviewId", update_columns: List[str] = None, retries: int = 2) -> int:
#         """Batch upsert (insert or update) rows using ON DUPLICATE KEY UPDATE.
        
#         Args:
#             table: Table name
#             columns: All column names being inserted
#             rows: Data rows (tuples)
#             unique_key: Column to check for duplicates (default: reviewId)
#             update_columns: Specific columns to update on duplicate. If None, updates all except unique_key
#             retries: Number of retry attempts on connection errors
#         """
#         if not rows:
#             return 0
        
#         last_error = None
        
#         for attempt in range(retries + 1):
#             conn = None
#             try:
#                 conn = self._get_connection()
#                 column_names = ", ".join([f"`{col}`" for col in columns])
#                 placeholders = ", ".join(["%s"] * len(columns))
                
#                 update_cols = update_columns if update_columns is not None else [col for col in columns if col != unique_key]
#                 update_clause = ", ".join([f"`{col}` = VALUES(`{col}`)" for col in update_cols])
#                 sql = f"INSERT INTO `{table}` ({column_names}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_clause}"
                
#                 with conn.cursor() as cursor:
#                     cursor.executemany(sql, rows)
#                     conn.commit()
#                     return cursor.rowcount
#             except (pymysql.err.OperationalError, pymysql.err.DatabaseError, AttributeError) as e:
#                 last_error = e
#                 if conn:
#                     try:
#                         conn.rollback()
#                     except Exception:
#                         pass
                
#                 if attempt < retries:
#                     self._connection = None
#                     backoff = 0.5 * (2 ** attempt)
#                     logger.warning(f"Batch upsert error (attempt {attempt + 1}/{retries + 1}), retrying in {backoff:.1f}s: {type(e).__name__}")
#                     time.sleep(backoff)
#                 else:
#                     logger.error(f"Batch upsert failed after {retries + 1} attempts: {e}")
#             except Exception as e:
#                 if conn:
#                     try:
#                         conn.rollback()
#                     except Exception:
#                         pass
#                 logger.error(f"Batch upsert execution error: {e}")
#                 raise
        
#         raise last_error if last_error else RuntimeError("Batch upsert failed")

#     def create_tables(self) -> None:
#         """Create all required tables if they don't exist."""
#         tables_sql = [
#             """
#             CREATE TABLE IF NOT EXISTS `token` (
#                 id INT AUTO_INCREMENT PRIMARY KEY,
#                 access_token LONGTEXT NOT NULL,
#                 refresh_token LONGTEXT,
#                 token_expiry DATETIME,
#                 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
#             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
#             """,
#             """
#             CREATE TABLE IF NOT EXISTS `locations` (
#                 id INT AUTO_INCREMENT PRIMARY KEY,
#                 name VARCHAR(255) NOT NULL UNIQUE,
#                 title VARCHAR(255),
#                 storeCode VARCHAR(100),
#                 status VARCHAR(50),
#                 primaryPhone VARCHAR(20),
#                 regionCode VARCHAR(10),
#                 administrativeArea VARCHAR(100),
#                 locality VARCHAR(100),
#                 postalCode VARCHAR(20),
#                 placeId VARCHAR(255),
#                 labels JSON,
#                 fetchedAt DATETIME,
#                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
#                 INDEX idx_name (name),
#                 INDEX idx_storeCode (storeCode)
#             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
#             """,
#             """
#             CREATE TABLE IF NOT EXISTS `location_reviews` (
#                 id BIGINT AUTO_INCREMENT PRIMARY KEY,
#                 name VARCHAR(255) NOT NULL,
#                 reviewId VARCHAR(255) NOT NULL UNIQUE,
#                 reviewer_displayName VARCHAR(255),
#                 reviewer_isAnonymous BOOLEAN,
#                 reviewer_profilePhotoUrl LONGTEXT,
#                 starRating VARCHAR(10),
#                 rating INT,
#                 comment LONGTEXT,
#                 createTime DATETIME,
#                 updateTime DATETIME,
#                 fetchedAt DATETIME,
#                 reviewReply LONGTEXT,
#                 title VARCHAR(255),
#                 sentiment VARCHAR(50),
#                 emotion VARCHAR(100),
#                 attributes LONGTEXT,
#                 context_sentiment VARCHAR(50),
#                 context_confidence DECIMAL(5,2),
#                 final_sentiment VARCHAR(50),
#                 quality_score INT,
#                 post_error LONGTEXT,
#                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                 INDEX idx_name (name),
#                 INDEX idx_reviewId (reviewId),
#                 INDEX idx_createTime (createTime)
#             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
#             """,
#             """
#             CREATE TABLE IF NOT EXISTS `location_ratings` (
#                 id INT AUTO_INCREMENT PRIMARY KEY,
#                 name VARCHAR(255) NOT NULL UNIQUE,
#                 review_count INT DEFAULT 0,
#                 rating_average DOUBLE,
#                 fetchedAt DATETIME,
#                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
#                 INDEX idx_name (name)
#             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
#             """,
#             """
#             CREATE TABLE IF NOT EXISTS `locations_metrics_batches` (
#                 id INT AUTO_INCREMENT PRIMARY KEY,
#                 batch_id VARCHAR(255) NOT NULL UNIQUE,
#                 createdAt DATETIME,
#                 account_id VARCHAR(255),
#                 start_date VARCHAR(20),
#                 end_date VARCHAR(20),
#                 location_count_requested INT,
#                 location_count_returned INT,
#                 errors_count INT,
#                 metrics_requested JSON,
#                 results LONGTEXT,
#                 errors LONGTEXT,
#                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                 INDEX idx_batch_id (batch_id),
#                 INDEX idx_account_id (account_id)
#             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
#             """,
#             """
#             CREATE TABLE IF NOT EXISTS `region` (
#                 id INT AUTO_INCREMENT PRIMARY KEY,
#                 storeCode VARCHAR(100) NOT NULL,
#                 title VARCHAR(255),
#                 name VARCHAR(255),
#                 region VARCHAR(100),
#                 createdAt DATETIME,
#                 modifiedAt DATETIME,
#                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
#                 UNIQUE KEY unique_store_modified (storeCode, modifiedAt),
#                 INDEX idx_storeCode (storeCode)
#             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
#             """,
#             """
#             CREATE TABLE IF NOT EXISTS `event_posts_batches` (
#                 id INT AUTO_INCREMENT PRIMARY KEY,
#                 batch_id VARCHAR(255) NOT NULL UNIQUE,
#                 createdAt DATETIME,
#                 account_id VARCHAR(255) NOT NULL,
#                 title_for_file VARCHAR(255),
#                 topic_type VARCHAR(50),
#                 location_count INT,
#                 locations JSON,
#                 results LONGTEXT,
#                 created_by VARCHAR(255),
#                 modified_by VARCHAR(255),
#                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
#                 INDEX idx_batch_id (batch_id),
#                 INDEX idx_account_id (account_id),
#                 INDEX idx_createdAt (createdAt)
#             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
#             """,
#             """
#             CREATE TABLE IF NOT EXISTS `review_analytics` (
#                 id BIGINT AUTO_INCREMENT PRIMARY KEY,
#                 location_name VARCHAR(255) NOT NULL,
#                 date DATE NOT NULL,
#                 total_reviews INT DEFAULT 0,
#                 positive_reviews INT DEFAULT 0,
#                 negative_reviews INT DEFAULT 0,
#                 neutral_reviews INT DEFAULT 0,
#                 average_rating DOUBLE,
#                 reply_rate DECIMAL(5,2),
#                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
#                 UNIQUE KEY unique_location_date (location_name, date),
#                 INDEX idx_location_name (location_name),
#                 INDEX idx_date (date),
#                 INDEX idx_location_date (location_name, date)
#             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
#             """,
#             """
#             CREATE TABLE IF NOT EXISTS `post_history` (
#                 id INT AUTO_INCREMENT PRIMARY KEY,
#                 batch_id VARCHAR(255) NOT NULL,
#                 post_id VARCHAR(255) NOT NULL,
#                 location_id VARCHAR(255) NOT NULL,
#                 action VARCHAR(50) NOT NULL,
#                 status VARCHAR(50) NOT NULL,
#                 modified_by VARCHAR(255),
#                 details JSON,
#                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                 INDEX idx_batch_id (batch_id),
#                 INDEX idx_post_id (post_id),
#                 INDEX idx_location_id (location_id),
#                 INDEX idx_action (action),
#                 INDEX idx_created_at (created_at)
#             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
#             """,
#             """
#             CREATE TABLE IF NOT EXISTS `review_history` (
#                 id BIGINT AUTO_INCREMENT PRIMARY KEY,
#                 review_id VARCHAR(255) NOT NULL,
#                 location_id VARCHAR(255) NOT NULL,
#                 action VARCHAR(50) NOT NULL,
#                 old_reply LONGTEXT,
#                 new_reply LONGTEXT,
#                 old_sentiment VARCHAR(50),
#                 new_sentiment VARCHAR(50),
#                 old_emotion VARCHAR(100),
#                 new_emotion VARCHAR(100),
#                 old_attributes LONGTEXT,
#                 new_attributes LONGTEXT,
#                 old_quality_score INT,
#                 new_quality_score INT,
#                 gmb_posted BOOLEAN DEFAULT FALSE,
#                 gmb_error LONGTEXT,
#                 modified_by VARCHAR(255),
#                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                 INDEX idx_review_id (review_id),
#                 INDEX idx_location_id (location_id),
#                 INDEX idx_action (action),
#                 INDEX idx_created_at (created_at)
#             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
#             """,
#         ]

#         tables_initialized = 0
#         for sql in tables_sql:
#             try:
#                 self.execute_update(sql)
#                 tables_initialized += 1
#                 logger.debug("Table created or already exists")
#             except Exception as e:
#                 error_msg = str(e)
#                 if "PermissionDenied" in error_msg or "DDL command denied" in error_msg:
#                     logger.warning(f"DDL permission denied (user may lack DDL rights): {e}")
#                     logger.warning("Tables must be created manually or by an admin user")
#                     continue
#                 else:
#                     logger.error(f"Error creating table: {e}")
#                     raise
        
#         logger.info(f"✅ Database tables initialized: {tables_initialized} tables ready")
        
#         try:
#             self.execute_update(
#                 "ALTER TABLE `location_reviews` ADD COLUMN reviewReply LONGTEXT AFTER fetchedAt"
#             )
#             logger.debug("✅ Added reviewReply column to location_reviews")
#         except Exception as e:
#             error_str = str(e)
#             if "Duplicate column name" not in error_str and "1060" not in error_str:
#                 logger.warning(f"❌ Could not add reviewReply column: {e}")
        
#         try:
#             self.execute_update(
#                 "ALTER TABLE `location_reviews` ADD COLUMN title VARCHAR(255) AFTER reviewReply"
#             )
#             logger.debug("✅ Added title column to location_reviews")
#         except Exception as e:
#             error_str = str(e)
#             if "Duplicate column name" not in error_str and "1060" not in error_str:
#                 logger.warning(f"❌ Could not add title column: {e}")
        
#         columns_to_add = [
#             ("sentiment", "VARCHAR(50) AFTER title"),
#             ("emotion", "VARCHAR(100) AFTER sentiment"),
#             ("attributes", "LONGTEXT AFTER emotion"),
#             ("context_sentiment", "VARCHAR(50) AFTER attributes"),
#             ("context_confidence", "DECIMAL(5,2) AFTER context_sentiment"),
#             ("final_sentiment", "VARCHAR(50) AFTER context_confidence"),
#             ("quality_score", "INT AFTER final_sentiment"),
#             ("post_error", "LONGTEXT AFTER quality_score"),
#         ]
        
#         for col_name, col_def in columns_to_add:
#             try:
#                 self.execute_update(
#                     f"ALTER TABLE `location_reviews` ADD COLUMN {col_name} {col_def}"
#                 )
#                 logger.debug(f"✅ Added {col_name} column to location_reviews")
#             except Exception as e:
#                 error_str = str(e)
#                 if "Duplicate column name" not in error_str and "1060" not in error_str:
#                     logger.warning(f"❌ Could not add {col_name} column: {e}")


# db = PlanetScaleDB()


# async def init_db():
#     """Initialize the database (create tables)."""
#     try:
#         loop = asyncio.get_running_loop()
#         await loop.run_in_executor(None, db.create_tables)
#         logger.info("Database initialized successfully")
#     except Exception as e:
#         logger.error(f"Failed to initialize database: {e}")
#         raise


# async def save_google_token(
#     access_token: str, refresh_token: Optional[str], expiry: Optional[datetime]
# ) -> None:
#     """Save Google OAuth tokens to the token table."""
#     loop = asyncio.get_running_loop()
#     sql = """
#     INSERT INTO token (access_token, refresh_token, token_expiry)
#     VALUES (%s, %s, %s)
#     ON DUPLICATE KEY UPDATE
#         access_token = VALUES(access_token),
#         refresh_token = VALUES(refresh_token),
#         token_expiry = VALUES(token_expiry),
#         updated_at = CURRENT_TIMESTAMP
#     """
#     await loop.run_in_executor(
#         None, db.execute_update, sql, (access_token, refresh_token, expiry)
#     )
#     logger.info("Saved Google tokens to database")


# def save_google_token_blocking(
#     access_token: str, refresh_token: Optional[str], expiry: Optional[datetime]
# ) -> None:
#     """Save Google OAuth tokens to the token table (blocking version)."""
#     sql = """
#     INSERT INTO token (access_token, refresh_token, token_expiry)
#     VALUES (%s, %s, %s)
#     ON DUPLICATE KEY UPDATE
#         access_token = VALUES(access_token),
#         refresh_token = VALUES(refresh_token),
#         token_expiry = VALUES(token_expiry),
#         updated_at = CURRENT_TIMESTAMP
#     """
#     db.execute_update(sql, (access_token, refresh_token, expiry))
#     logger.info("Saved Google tokens to database")


# async def get_google_credentials() -> Credentials:
#     """Load Google credentials from database and refresh if needed."""
#     loop = asyncio.get_running_loop()

#     def _get_creds():
#         result = db.execute_query("SELECT * FROM token ORDER BY updated_at DESC LIMIT 1")
#         if not result:
#             raise RuntimeError("Google credentials not found. Authenticate first.")

#         token_row = result[0]
#         credentials = Credentials(
#             token=token_row["access_token"],
#             refresh_token=token_row["refresh_token"],
#             token_uri="https://oauth2.googleapis.com/token",
#             client_id=settings.GOOGLE_CLIENT_ID,
#             client_secret=settings.GOOGLE_CLIENT_SECRET,
#             expiry=token_row["token_expiry"],
#         )

#         if (not credentials.token) or getattr(credentials, "expired", False):
#             credentials.refresh(GoogleRequest())
#             db.execute_update(
#                 "INSERT INTO token (access_token, refresh_token, token_expiry) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE access_token = VALUES(access_token), refresh_token = VALUES(refresh_token), token_expiry = VALUES(token_expiry)",
#                 (credentials.token, credentials.refresh_token, credentials.expiry),
#             )
#             logger.info("Refreshed and persisted Google credentials")

#         return credentials

#     return await loop.run_in_executor(None, _get_creds)


# async def append_reviews_to_db(
#     review_rows: List[Dict[str, Any]], namespace: str = None
# ) -> int:
#     """Append review rows to location_reviews table in PlanetScale."""
#     if not review_rows:
#         return 0

#     loop = asyncio.get_running_loop()

#     def _append():
#         sql = """
#         INSERT INTO location_reviews 
#         (name, reviewId, reviewer_displayName, reviewer_isAnonymous, 
#          reviewer_profilePhotoUrl, starRating, rating, comment, 
#          createTime, updateTime, fetchedAt, reviewReply, title,
#          sentiment, emotion, attributes, context_sentiment, context_confidence,
#          final_sentiment, quality_score, post_error)
#         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#         """
#         count = 0
#         for row in review_rows:
#             try:
#                 db.execute_update(
#                     sql,
#                     (
#                         row.get("name"),
#                         row.get("reviewId"),
#                         row.get("reviewer_displayName"),
#                         row.get("reviewer_isAnonymous"),
#                         row.get("reviewer_profilePhotoUrl"),
#                         row.get("starRating"),
#                         row.get("rating"),
#                         row.get("comment"),
#                         row.get("createTime"),
#                         row.get("updateTime"),
#                         row.get("fetchedAt"),
#                         row.get("reviewReply"),
#                         row.get("title"),
#                         row.get("sentiment"),
#                         row.get("emotion"),
#                         row.get("attributes"),
#                         row.get("context_sentiment"),
#                         row.get("context_confidence"),
#                         row.get("final_sentiment"),
#                         row.get("quality_score"),
#                         row.get("post_error"),
#                     ),
#                 )
#                 count += 1
#             except pymysql.err.IntegrityError:
#                 pass
#         return count

#     return await loop.run_in_executor(None, _append)


# async def append_lifetime_reviews(
#     review_rows: List[Dict[str, Any]], namespace: str = None
# ) -> int:
#     """Append rows to location_reviews_lifetime table (just an alias to location_reviews for now)."""
#     return await append_reviews_to_db(review_rows, namespace)


# async def upsert_rating_summary(
#     summary_rows: List[Dict[str, Any]], namespace: str = None
# ) -> int:
#     """Upsert rating summaries into location_ratings table."""
#     if not summary_rows:
#         return 0

#     loop = asyncio.get_running_loop()

#     def _upsert():
#         sql = """
#         INSERT INTO location_ratings (name, review_count, rating_average, fetchedAt)
#         VALUES (%s, %s, %s, %s)
#         ON DUPLICATE KEY UPDATE
#             review_count = VALUES(review_count),
#             rating_average = VALUES(rating_average),
#             fetchedAt = VALUES(fetchedAt),
#             updated_at = CURRENT_TIMESTAMP
#         """
#         count = 0
#         for row in summary_rows:
#             db.execute_update(
#                 sql,
#                 (
#                     row.get("name"),
#                     row.get("review_count"),
#                     row.get("rating_average"),
#                     row.get("fetchedAt"),
#                 ),
#             )
#             count += 1
#         return count

#     return await loop.run_in_executor(None, _upsert)


# async def write_locations_to_db(
#     normalized: List[Dict[str, Any]], namespace: str = None
# ) -> int:
#     """Write/upsert locations to PlanetScale database."""
#     if not normalized:
#         return 0

#     loop = asyncio.get_running_loop()

#     def _write():
#         sql = """
#         INSERT INTO locations 
#         (name, title, storeCode, status, primaryPhone, regionCode, 
#          administrativeArea, locality, postalCode, placeId, labels, fetchedAt)
#         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#         ON DUPLICATE KEY UPDATE
#             title = VALUES(title),
#             storeCode = VALUES(storeCode),
#             status = VALUES(status),
#             primaryPhone = VALUES(primaryPhone),
#             regionCode = VALUES(regionCode),
#             administrativeArea = VALUES(administrativeArea),
#             locality = VALUES(locality),
#             postalCode = VALUES(postalCode),
#             placeId = VALUES(placeId),
#             labels = VALUES(labels),
#             fetchedAt = VALUES(fetchedAt),
#             updated_at = CURRENT_TIMESTAMP
#         """
#         count = 0
#         for idx, row in enumerate(normalized):
#             try:
#                 labels = json.dumps(row.get("labels", []))
#             except Exception as e:
#                 logger.error(f"Failed to serialize labels for row {idx}: {e}, labels={row.get('labels')}")
#                 labels = "[]"
            
#             try:
#                 db.execute_update(
#                     sql,
#                     (
#                         row.get("name"),
#                         row.get("title"),
#                         row.get("storeCode"),
#                         row.get("status"),
#                         row.get("primaryPhone"),
#                         row.get("regionCode"),
#                         row.get("administrativeArea"),
#                         row.get("locality"),
#                         row.get("postalCode"),
#                         row.get("placeId"),
#                         labels,
#                         row.get("fetchedAt"),
#                     ),
#                 )
#                 count += 1
#             except Exception as e:
#                 logger.error(f"Failed to insert row {idx} (name={row.get('name')}): {e}")
#                 raise
#         return count

#     return await loop.run_in_executor(None, _write)


# async def delete_location_from_db(location_name: str, namespace: str = None) -> bool:
#     """Delete a location by name from PlanetScale database."""
#     loop = asyncio.get_running_loop()

#     def _delete():
#         sql = "DELETE FROM locations WHERE name = %s"
#         db.execute_update(sql, (location_name,))
#         logger.info(f"Deleted location: {location_name}")
#         return True

#     return await loop.run_in_executor(None, _delete)


# async def read_location_from_db(
#     location_name: str, namespace: str = None
# ) -> Optional[Dict[str, Any]]:
#     """Read a single location by name from PlanetScale database."""
#     loop = asyncio.get_running_loop()

#     def _read():
#         sql = "SELECT * FROM locations WHERE name = %s LIMIT 1"
#         result = db.execute_query(sql, (location_name,))
#         if result:
#             location = result[0]
#             if location.get("labels"):
#                 try:
#                     location["labels"] = json.loads(location["labels"])
#                 except (json.JSONDecodeError, TypeError):
#                     location["labels"] = []
#             return location
#         return None

#     return await loop.run_in_executor(None, _read)


# async def bulk_upsert_review_analytics(
#     analytics_rows: List[Dict[str, Any]]
# ) -> int:
#     """Bulk upsert review analytics data for fast aggregation."""
#     if not analytics_rows:
#         return 0

#     loop = asyncio.get_running_loop()

#     def _upsert():
#         sql = """
#         INSERT INTO review_analytics 
#         (location_name, date, total_reviews, positive_reviews, negative_reviews, 
#          neutral_reviews, average_rating, reply_rate)
#         VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
#         ON DUPLICATE KEY UPDATE
#             total_reviews = VALUES(total_reviews),
#             positive_reviews = VALUES(positive_reviews),
#             negative_reviews = VALUES(negative_reviews),
#             neutral_reviews = VALUES(neutral_reviews),
#             average_rating = VALUES(average_rating),
#             reply_rate = VALUES(reply_rate),
#             updated_at = CURRENT_TIMESTAMP
#         """
#         count = 0
#         for row in analytics_rows:
#             db.execute_update(
#                 sql,
#                 (
#                     row.get("location_name"),
#                     row.get("date"),
#                     row.get("total_reviews", 0),
#                     row.get("positive_reviews", 0),
#                     row.get("negative_reviews", 0),
#                     row.get("neutral_reviews", 0),
#                     row.get("average_rating"),
#                     row.get("reply_rate"),
#                 ),
#             )
#             count += 1
#         return count

#     return await loop.run_in_executor(None, _upsert)


# async def get_location_analytics(
#     location_name: str, start_date: str = None, end_date: str = None
# ) -> List[Dict[str, Any]]:
#     """Get analytics data for a location within a date range."""
#     loop = asyncio.get_running_loop()

#     def _get():
#         sql = """
#         SELECT * FROM review_analytics 
#         WHERE location_name = %s
#         """
#         params = [location_name]
        
#         if start_date:
#             sql += " AND date >= %s"
#             params.append(start_date)
#         if end_date:
#             sql += " AND date <= %s"
#             params.append(end_date)
        
#         sql += " ORDER BY date DESC"
        
#         return db.execute_query(sql, tuple(params))

#     return await loop.run_in_executor(None, _get)


# async def get_batch_analytics(
#     account_id: str, limit: int = 100, offset: int = 0
# ) -> tuple:
#     """Get event post batch analytics for an account."""
#     loop = asyncio.get_running_loop()

#     def _get():
#         count_sql = "SELECT COUNT(*) as cnt FROM event_posts_batches WHERE account_id = %s"
#         count_result = db.execute_query(count_sql, (account_id,))
#         total = count_result[0]["cnt"] if count_result else 0
        
#         sql = """
#         SELECT 
#             batch_id, createdAt, topic_type, location_count,
#             created_by, modified_by
#         FROM event_posts_batches 
#         WHERE account_id = %s 
#         ORDER BY createdAt DESC 
#         LIMIT %s OFFSET %s
#         """
        
#         results = db.execute_query(sql, (account_id, limit, offset))
#         return results, total

#     return await loop.run_in_executor(None, _get)


# def close_db() -> None:
#     """Close the database connection."""
#     db.close()
