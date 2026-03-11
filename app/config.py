# from typing import Optional, Tuple, List, Dict, Any
# import logging
# from pathlib import Path
# from dotenv import load_dotenv
# from pydantic_settings import BaseSettings
# from pydantic import ValidationError, Field
# import os

# logger = logging.getLogger(__name__)

# env_path = Path("config.env")
# if env_path.exists():
#     load_dotenv(env_path)
#     print(f"Loaded environment from {env_path}")
# else:
#     print(f"No environment file found at {env_path}")


# class Settings(BaseSettings):
#     # Required (your core vars)
#     GOOGLE_CLIENT_ID: str
#     GOOGLE_CLIENT_SECRET: str
#     GOOGLE_REDIRECT_URI: str
#     GOOGLE_ACCOUNT_ID: str
#     GOOGLE_CREDENTIALS_FILE: str = "config.credentials.json"

#     ICEBERG_TOKEN: str
#     ICEBERG_WAREHOUSE: str
#     ICEBERG_CATALOG_URI: str
#     ICEBERG_NAMESPACE: str = "gmb_reviews"

#     # Sync / retries
#     SYNC_MAX_ATTEMPTS: int = 5
#     SYNC_BASE_BACKOFF: float = 0.5
#     SYNC_MAX_PAGE_SIZE: int = 200

#     # Scheduler
#     TOKEN_REFRESH_INTERVAL_SECONDS: int = 60 * 10

#     LOG_LEVEL: str = "INFO"

#     # OPTIONAL / extra fields
#     FRONTEND_URL: Optional[str] = None
#     R2_ENDPOINT_URL: Optional[str] = None
#     R2_ACCESS_KEY_ID: Optional[str] = None
#     R2_SECRET_ACCESS_KEY: Optional[str] = None
#     R2_BUCKET_NAME: Optional[str] = None
#     R2_PUBLIC_BASE: Optional[str] = None

#     ENABLE_JOBS: Optional[bool] = None
#     OAUTHLIB_INSECURE_TRANSPORT: Optional[str] = None

#     OPENAI_API_KEY: Optional[str] = None
#     GENAI_API_KEY: Optional[str] = None
#     CF_NAMESPACE: Optional[str] = None
#     CF_TABLE: Optional[str] = None

#     # Scheduling / sync settings
#     SYNC_DAILY_ENABLED: bool = True
#     SYNC_DAILY_HOUR: int = 2
#     SYNC_DAILY_MINUTE: int = 0
#     SYNC_INTERNAL_ENDPOINT: str = "http://127.0.0.1:8001/auth/v2/sync/locations"
#     SYNC_DAILY_ACCOUNT_ID: Optional[str] = None

#     # Reviews periodic sync
#     SYNC_REVIEWS_ENABLED: bool = True
#     SYNC_REVIEWS_INTERVAL_MINUTES: int = 30
#     SYNC_REVIEWS_INTERNAL_ENDPOINT: str = "http://127.0.0.1:8001/auth/v2/sync/reviews"
#     SYNC_REVIEWS_ACCOUNT_ID: Optional[str] = None
#     SYNC_REVIEWS_PAGE_SIZE: int = 50

#     SYNC_HTTP_MAX_RETRIES: int = 3
#     SYNC_HTTP_BASE_BACKOFF: float = 2.0

#     AUTH_USERINFO_URL: str = ""

#     # Catalog settings - with proper type annotations
#     ICEBERG_CATALOG_NAME: str = "cloudflare"

#     # Pydantic v2 settings
#     model_config = {
#         "env_file": ".env",
#         "env_file_encoding": "utf-8",
#         "extra": "allow",
#     }

#     @property
#     def ICEBERG_CATALOG_CONFIG(self) -> Dict[str, Any]:
#         """
#         Dynamically build catalog config using instance values.
#         """
#         return {
#             "type": "rest",
#             "uri": self.ICEBERG_CATALOG_URI,
#             "token": self.ICEBERG_TOKEN,
#             "warehouse": self.ICEBERG_WAREHOUSE,
#         }


# def load_settings() -> Tuple[Optional[Settings], Optional[List[str]]]:
#     """
#     Try to instantiate Settings. Returns (settings, None) on success,
#     or (None, errors) on ValidationError where errors is a list of strings.
#     """
#     try:
#         s = Settings()
#         return s, None
#     except ValidationError as exc:
#         errs: List[str] = []
#         for e in exc.errors():
#             loc = ".".join([str(x) for x in e.get("loc", [])])
#             msg = e.get("msg", str(e))
#             errs.append(f"{loc}: {msg}")
#         return None, errs


# try:
#     settings: Optional[Settings] = Settings()
# except ValidationError as exc:
#     logger.warning(
#         "app.config: Settings() failed to load at import time. "
#         "Missing or invalid env vars: %s", exc
#     )
#     settings = None
# except Exception as exc:  # defensive: unexpected errors
#     logger.exception("app.config: unexpected error instantiating Settings(): %s", exc)
#     settings = None


# # explicit exports
# __all__ = ["Settings", "load_settings", "settings"]


from typing import Optional, Tuple, List, Dict, Any
import logging
import json
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from pydantic import ValidationError, Field, field_validator
import os

logger = logging.getLogger(__name__)

env_path = Path("config.env")
if env_path.exists():
    load_dotenv(env_path)
    print(f"Loaded environment from {env_path}")
else:
    print(f"No environment file found at {env_path}")


class Settings(BaseSettings):
    # Required (your core vars)
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str
    GOOGLE_ACCOUNT_ID: str = "114171310563376909370"
    GOOGLE_CREDENTIALS_FILE: str = "config.credentials.json"

    PLANETSCALE_HOST: str
    PLANETSCALE_USER: str
    PLANETSCALE_PASSWORD: str
    PLANETSCALE_DATABASE: str

    # Sync / retries
    SYNC_MAX_ATTEMPTS: int = 5
    SYNC_BASE_BACKOFF: float = 0.5
    SYNC_MAX_PAGE_SIZE: int = 200

    # Scheduler
    TOKEN_REFRESH_INTERVAL_SECONDS: int = 60 * 10

    LOG_LEVEL: str = "INFO"

    # OPTIONAL / extra fields
    FRONTEND_URL: Optional[str] = None
    R2_ENDPOINT: Optional[str] = None
    R2_ENDPOINT_URL: Optional[str] = None
    R2_ACCESS_KEY_ID: Optional[str] = None
    R2_SECRET_ACCESS_KEY: Optional[str] = None
    R2_BUCKET_NAME: Optional[str] = "metrics-archive"
    R2_PUBLIC_BASE: Optional[str] = None

    ENABLE_JOBS: Optional[bool] = None
    OAUTHLIB_INSECURE_TRANSPORT: Optional[str] = None

    OPENAI_API_KEY: Optional[str] = None
    GENAI_API_KEY: Optional[str] = None
    CF_NAMESPACE: Optional[str] = None
    CF_TABLE: Optional[str] = None

    # Scheduling / sync settings
    SYNC_DAILY_ENABLED: bool = True
    SYNC_DAILY_HOUR: int = 2
    SYNC_DAILY_MINUTE: int = 0
    SYNC_INTERNAL_ENDPOINT: str = "http://127.0.0.1:8001/auth/v2/sync/locations"
    SYNC_DAILY_ACCOUNT_ID: Optional[str] = None

    # Reviews periodic sync
    SYNC_REVIEWS_ENABLED: bool = True
    SYNC_REVIEWS_INTERVAL_MINUTES: int = 30
    SYNC_REVIEWS_INTERNAL_ENDPOINT: str = "http://127.0.0.1:8001/auth/v2/sync/reviews"
    SYNC_REVIEWS_ACCOUNT_ID: Optional[str] = None
    SYNC_REVIEWS_PAGE_SIZE: int = 50

    SYNC_HTTP_MAX_RETRIES: int = 3
    SYNC_HTTP_BASE_BACKOFF: float = 2.0

    AUTH_USERINFO_URL: str = ""

    ADMIN_KEY: str = "admin"

    # Pydantic v2 settings
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "allow",
    }

    @field_validator("GOOGLE_CREDENTIALS_FILE")
    @classmethod
    def validate_credentials_file(cls, v: str) -> str:
        """Validate that the credentials file exists and is valid JSON."""
        creds_path = Path(v)

        if not creds_path.exists():
            logger.error(f"❌ Credentials file not found: {creds_path.absolute()}")
            logger.error(f"Current directory: {Path.cwd()}")
            logger.error(f"Directory contents: {list(Path('.').glob('*'))}")
            raise ValueError(f"Credentials file not found: {v}")

        logger.info(f"✅ Credentials file found: {creds_path.absolute()}")
        logger.info(f"File size: {creds_path.stat().st_size} bytes")

        # Validate JSON structure
        try:
            with open(creds_path, "r") as f:
                creds_data = json.load(f)

            logger.info(f"✅ Credentials JSON is valid")
            logger.info(f"Top-level keys: {list(creds_data.keys())}")

            # Check for required OAuth structure
            if "web" not in creds_data and "installed" not in creds_data:
                logger.error(
                    f"❌ Invalid credentials structure. Expected 'web' or 'installed' key."
                )
                logger.error(f"Found keys: {list(creds_data.keys())}")
                raise ValueError("Invalid credentials file structure")

            # Get the actual credentials object
            creds_obj = creds_data.get("web") or creds_data.get("installed")
            required_keys = ["client_id", "client_secret"]
            missing_keys = [k for k in required_keys if k not in creds_obj]

            if missing_keys:
                logger.error(f"❌ Missing required keys in credentials: {missing_keys}")
                raise ValueError(
                    f"Credentials file missing required keys: {missing_keys}"
                )

            logger.info(f"✅ Credentials file structure is valid")

        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON in credentials file: {e}")
            with open(creds_path, "r") as f:
                content = f.read(500)
                logger.error(f"File content (first 500 chars): {content}")
            raise ValueError(f"Invalid JSON in credentials file: {e}")
        except Exception as e:
            logger.error(f"❌ Error validating credentials file: {e}")
            raise

        return v

    @property
    def PLANETSCALE_CONNECTION_CONFIG(self) -> Dict[str, Any]:
        """
        Dynamically build PlanetScale connection config using instance values.
        """
        return {
            "host": self.PLANETSCALE_HOST,
            "user": self.PLANETSCALE_USER,
            "password": self.PLANETSCALE_PASSWORD,
            "database": self.PLANETSCALE_DATABASE,
            "ssl_verify_cert": True,
            "ssl_verify_identity": True,
        }


def load_settings() -> Tuple[Optional[Settings], Optional[List[str]]]:
    """
    Try to instantiate Settings. Returns (settings, None) on success,
    or (None, errors) on ValidationError where errors is a list of strings.
    """
    # Check credentials file existence BEFORE trying to load settings
    creds_file = Path("config.credentials.json")
    logger.info("=" * 60)
    logger.info("🔍 Checking credentials file...")
    logger.info("=" * 60)

    if creds_file.exists():
        logger.info(f"✅ Credentials file exists: {creds_file.absolute()}")
        logger.info(f"📦 File size: {creds_file.stat().st_size} bytes")

        # Validate JSON
        try:
            with open(creds_file, "r") as f:
                creds_data = json.load(f)
                logger.info(f"✅ Credentials JSON is valid")
                logger.info(f"📋 Top-level keys: {list(creds_data.keys())}")

                if "web" in creds_data:
                    logger.info(f"🔑 Found 'web' credentials")
                    logger.info(f"🔑 Web keys: {list(creds_data['web'].keys())}")
                elif "installed" in creds_data:
                    logger.info(f"🔑 Found 'installed' credentials")
                    logger.info(
                        f"🔑 Installed keys: {list(creds_data['installed'].keys())}"
                    )
                else:
                    logger.error(f"❌ No 'web' or 'installed' key found!")

        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON in credentials file: {e}")
            with open(creds_file, "r") as f:
                content = f.read(500)
                logger.error(f"File content (first 500 chars): {content}")
        except Exception as e:
            logger.error(f"❌ Error reading credentials file: {e}", exc_info=True)
    else:
        logger.error(f"❌ Credentials file NOT FOUND: {creds_file.absolute()}")
        logger.error(f"📁 Current directory: {Path.cwd()}")
        logger.error(f"📂 Directory contents: {list(Path('.').glob('config*'))}")

    logger.info("=" * 60)

    try:
        s = Settings()
        logger.info("✅ Settings loaded successfully")
        return s, None
    except ValidationError as exc:
        errs: List[str] = []
        for e in exc.errors():
            loc = ".".join([str(x) for x in e.get("loc", [])])
            msg = e.get("msg", str(e))
            errs.append(f"{loc}: {msg}")
        logger.error(f"❌ Settings validation failed: {errs}")
        return None, errs


"""

"""
try:
    settings: Optional[Settings] = Settings()
    if settings:
        logger.info("✅ Global settings instance created successfully")
except ValidationError as exc:
    logger.warning(
        "app.config: Settings() failed to load at import time. "
        "Missing or invalid env vars: %s",
        exc,
    )
    settings = None 
except Exception as exc:  # defensive: unexpected errors
    logger.exception("app.config: unexpected error instantiating Settings(): %s", exc)
    settings = None


# explicit exports
__all__ = ["Settings", "load_settings", "settings"]
