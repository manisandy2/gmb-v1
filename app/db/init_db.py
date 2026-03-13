import asyncio
import logging

from app.db.connection import db

logger = logging.getLogger(__name__)


def create_tables() -> None:
    """
    Create all required tables if they do not exist.
    """

    tables_sql = [

        """
        CREATE TABLE IF NOT EXISTS `token` (
            id INT AUTO_INCREMENT PRIMARY KEY,
            access_token LONGTEXT NOT NULL,
            refresh_token LONGTEXT,
            token_expiry DATETIME,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP 
            ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 
        COLLATE=utf8mb4_unicode_ci
        """,

        """
        CREATE TABLE IF NOT EXISTS `locations` (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL UNIQUE,
            title VARCHAR(255),
            storeCode VARCHAR(100),
            status VARCHAR(50),
            primaryPhone VARCHAR(20),
            regionCode VARCHAR(10),
            administrativeArea VARCHAR(100),
            locality VARCHAR(100),
            postalCode VARCHAR(20),
            placeId VARCHAR(255),
            labels JSON,
            fetchedAt DATETIME,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP 
            ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_name (name),
            INDEX idx_storeCode (storeCode)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 
        COLLATE=utf8mb4_unicode_ci
        """,

        """
        CREATE TABLE IF NOT EXISTS `location_reviews` (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            reviewId VARCHAR(255) NOT NULL UNIQUE,
            reviewer_displayName VARCHAR(255),
            reviewer_isAnonymous BOOLEAN,
            reviewer_profilePhotoUrl LONGTEXT,
            starRating VARCHAR(10),
            rating INT,
            comment LONGTEXT,
            createTime DATETIME,
            updateTime DATETIME,
            fetchedAt DATETIME,
            reviewReply LONGTEXT,
            title VARCHAR(255),
            sentiment VARCHAR(50),
            emotion VARCHAR(100),
            attributes LONGTEXT,
            context_sentiment VARCHAR(50),
            context_confidence DECIMAL(5,2),
            final_sentiment VARCHAR(50),
            quality_score INT,
            post_error LONGTEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_name (name),
            INDEX idx_reviewId (reviewId),
            INDEX idx_createTime (createTime)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 
        COLLATE=utf8mb4_unicode_ci
        """,

        """
        CREATE TABLE IF NOT EXISTS `location_ratings` (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL UNIQUE,
            review_count INT DEFAULT 0,
            rating_average DOUBLE,
            fetchedAt DATETIME,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP 
            ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_name (name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 
        COLLATE=utf8mb4_unicode_ci
        """,

        """
        CREATE TABLE IF NOT EXISTS `review_analytics` (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            location_name VARCHAR(255) NOT NULL,
            date DATE NOT NULL,
            total_reviews INT DEFAULT 0,
            positive_reviews INT DEFAULT 0,
            negative_reviews INT DEFAULT 0,
            neutral_reviews INT DEFAULT 0,
            average_rating DOUBLE,
            reply_rate DECIMAL(5,2),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP 
            ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY unique_location_date (location_name, date),
            INDEX idx_location_name (location_name),
            INDEX idx_date (date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 
        COLLATE=utf8mb4_unicode_ci
        """,
    ]

    tables_created = 0

    for sql in tables_sql:

        try:
            db.execute_update(sql)
            tables_created += 1
            logger.debug("Table ready")

        except Exception as e:

            error_msg = str(e)

            if "DDL command denied" in error_msg:
                logger.warning(
                    "Database user does not have permission to create tables."
                )
                continue

            logger.error(f"Table creation failed: {e}")
            raise

    logger.info(f"Database initialized successfully ({tables_created} tables)")


async def init_db():
    """
    Async wrapper for database initialization.
    """

    try:
        loop = asyncio.get_running_loop()

        await loop.run_in_executor(
            None,
            create_tables
        )

        logger.info("Database initialization completed")

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise