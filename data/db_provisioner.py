"""
DB provisioner: ensures data/conflux.db exists before dashboard reads from it.

Two modes:
- LOCAL (default): conflux.db is already at data/conflux.db. No-op.
- CLOUD: conflux.db doesn't exist locally; download from R2 if credentials
  are present in environment variables.

The dashboard calls ensure_db_exists() once at startup. Streamlit's
@st.cache_resource ensures the download happens at most once per container
lifetime, not on every page load.

Environment variables (only needed for CLOUD mode):
    R2_ACCESS_KEY_ID
    R2_SECRET_ACCESS_KEY
    R2_ENDPOINT_URL
    R2_BUCKET_NAME (defaults to 'conflux-data')
"""

import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/conflux.db")
R2_OBJECT_KEY = "conflux.db"


class DBProvisionError(Exception):
    """Raised when DB cannot be provisioned (neither local nor cloud)."""
    pass


def ensure_db_exists(db_path: Path = DEFAULT_DB_PATH) -> Path:
    """
    Ensure the SQLite DB exists at db_path.
    
    If the file already exists locally, return immediately (local mode).
    Otherwise, try to download from R2 if credentials are configured (cloud mode).
    
    Raises DBProvisionError if neither path works.
    """
    db_path = Path(db_path)
    
    # --- Local mode: file already exists ---
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        logger.info(f"DB exists locally at {db_path} ({size_mb:.2f} MB)")
        return db_path
    
    # --- Cloud mode: try to download from R2 ---
    logger.info(f"DB not found at {db_path}; checking for R2 credentials")
    
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    endpoint_url = os.getenv("R2_ENDPOINT_URL")
    bucket_name = os.getenv("R2_BUCKET_NAME", "conflux-data")
    
    if not all([access_key, secret_key, endpoint_url]):
        raise DBProvisionError(
            f"DB not found at {db_path} and R2 credentials not configured. "
            "Cannot provision database. "
            "For local dev: run `python -m scripts.run_daily` to create DB. "
            "For cloud deploy: set R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, "
            "R2_ENDPOINT_URL in environment."
        )
    
    logger.info(f"Downloading DB from R2 bucket={bucket_name} endpoint={endpoint_url}")
    
    # Ensure target directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        s3.download_file(bucket_name, R2_OBJECT_KEY, str(db_path))
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        raise DBProvisionError(
            f"Failed to download DB from R2: {error_code}. "
            f"Endpoint: {endpoint_url}, Bucket: {bucket_name}. "
            f"Details: {e}"
        )
    
    size_mb = db_path.stat().st_size / (1024 * 1024)
    logger.info(f"DB downloaded successfully to {db_path} ({size_mb:.2f} MB)")
    
    return db_path