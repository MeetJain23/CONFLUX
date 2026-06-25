"""
Upload data/conflux.db to Cloudflare R2 bucket.

Run this:
- After a fresh `python -m scripts.run_daily` when you want the deployed
  dashboard to show today's scores
- After universe expansion to update the deployed view
- Any time the deployed dashboard's data should refresh

The deployed Streamlit Cloud app will download this DB on container
startup. So uploading here = updating production.

Reads R2 credentials from .env (local) or os.environ (CI).
"""

import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

# --- Config ---
DB_PATH = Path("data/conflux.db")
R2_OBJECT_KEY = "conflux.db"  # name of the file inside the bucket

R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "conflux-data")


def main():
    # --- Validate config ---
    missing = []
    if not R2_ACCESS_KEY_ID:
        missing.append("R2_ACCESS_KEY_ID")
    if not R2_SECRET_ACCESS_KEY:
        missing.append("R2_SECRET_ACCESS_KEY")
    if not R2_ENDPOINT_URL:
        missing.append("R2_ENDPOINT_URL")
    
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        print("Add them to .env file (gitignored).")
        sys.exit(1)
    
    if not DB_PATH.exists():
        print(f"ERROR: DB file not found at {DB_PATH}")
        sys.exit(1)
    
    db_size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    print(f"DB file: {DB_PATH} ({db_size_mb:.2f} MB)")
    
    # --- Connect to R2 ---
    print(f"Connecting to R2 endpoint: {R2_ENDPOINT_URL}")
    s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",  # R2 uses 'auto' as the region
    )
    
    # --- Test bucket access ---
    try:
        s3.head_bucket(Bucket=R2_BUCKET_NAME)
        print(f"Bucket access verified: {R2_BUCKET_NAME}")
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        print(f"ERROR: Cannot access bucket {R2_BUCKET_NAME}")
        print(f"  Error code: {error_code}")
        print(f"  Details: {e}")
        print()
        print("Common causes:")
        print("  - Wrong bucket name in R2_BUCKET_NAME")
        print("  - API token doesn't have access to this bucket")
        print("  - Endpoint URL wrong")
        sys.exit(1)
    
    # --- Upload ---
    print(f"Uploading {DB_PATH} to s3://{R2_BUCKET_NAME}/{R2_OBJECT_KEY}...")
    try:
        s3.upload_file(
            str(DB_PATH),
            R2_BUCKET_NAME,
            R2_OBJECT_KEY,
        )
        print(f"Upload successful.")
    except ClientError as e:
        print(f"ERROR: Upload failed: {e}")
        sys.exit(1)
    
    # --- Verify by getting object metadata ---
    try:
        response = s3.head_object(Bucket=R2_BUCKET_NAME, Key=R2_OBJECT_KEY)
        size_mb = response["ContentLength"] / (1024 * 1024)
        last_modified = response["LastModified"]
        print()
        print("=== Upload verified ===")
        print(f"  Object: {R2_OBJECT_KEY}")
        print(f"  Size: {size_mb:.2f} MB")
        print(f"  Last modified: {last_modified}")
        print(f"  Bucket: {R2_BUCKET_NAME}")
    except ClientError as e:
        print(f"WARNING: Upload succeeded but verification failed: {e}")
    
    print()
    print("Done. The deployed dashboard will pick up this DB on next container start.")


if __name__ == "__main__":
    main()