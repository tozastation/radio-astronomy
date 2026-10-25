#!/usr/bin/env python3
"""
SRE 向け Garage S3 フルバックアップ復元 (Disaster Recovery) CLI

Google Drive 等に退避した .tar / .tar.gz アーカイブから、
Garage S3 (results/, archive/, golden/, raw/) のオブジェクトを直接ストリーミング復元する。
"""

import os
import sys
import argparse
import tarfile
from datetime import datetime, timezone

try:
    import boto3
except ImportError:
    print("Error: boto3 is required. Install with `pip install boto3`", file=sys.stderr)
    sys.exit(1)


def restore_backup(
    tar_path: str,
    endpoint_url: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    region: str = "garage",
    dry_run: bool = False,
):
    if not os.path.isfile(tar_path):
        print(f"Error: Backup file not found: {tar_path}", file=sys.stderr)
        sys.exit(1)

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )

    print(f"=== Starting Disaster Recovery Restore ===")
    print(f"Source Archive : {tar_path}")
    print(f"Target S3      : {endpoint_url}/{bucket}")
    print(f"Dry Run        : {dry_run}\n")

    restored_count = 0
    total_bytes = 0

    with tarfile.open(tar_path, mode="r:*") as tf:
        members = [m for m in tf.getmembers() if m.isfile()]
        total_members = len(members)
        print(f"Found {total_members} files in archive.\n")

        for idx, m in enumerate(members, start=1):
            key = m.name.lstrip("/")
            if ".." in key:
                print(f"[{idx}/{total_members}] Skipping unsafe path: {key}")
                continue

            size_mb = m.size / (1024 * 1024)
            if dry_run:
                print(f"[{idx}/{total_members}] [DRY-RUN] Would upload: s3://{bucket}/{key} ({size_mb:.2f} MB)")
            else:
                f = tf.extractfile(m)
                if f:
                    s3.upload_fileobj(f, Bucket=bucket, Key=key)
                    print(f"[{idx}/{total_members}] Restored: s3://{bucket}/{key} ({size_mb:.2f} MB)")
                else:
                    print(f"[{idx}/{total_members}] Warning: could not extract {key}")

            restored_count += 1
            total_bytes += m.size

    print(f"\n=== Restore Finished ===")
    print(f"Total Objects Restored: {restored_count}")
    print(f"Total Transferred     : {total_bytes / (1024 * 1024):.2f} MB")


def main():
    parser = argparse.ArgumentParser(description="Garage S3 Disaster Recovery Restore CLI")
    parser.add_argument("backup_file", help="Path to backup .tar or .tar.gz archive")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("GARAGE_S3_ENDPOINT", "http://localhost:30900"),
        help="S3 Endpoint URL (default: http://localhost:30900)",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("GARAGE_BUCKET", "satellite-recordings"),
        help="Target S3 Bucket (default: satellite-recordings)",
    )
    parser.add_argument(
        "--access-key",
        default=os.environ.get("GARAGE_ACCESS_KEY", "GKc936f490b86ca470a41c5ad7"),
        help="S3 Access Key",
    )
    parser.add_argument(
        "--secret-key",
        default=os.environ.get("GARAGE_SECRET_KEY", "aa6934eef383fd8768368eb05f1377b803785922ccf2ebe9eaf0dfc69b0d7c47"),
        help="S3 Secret Key",
    )
    parser.add_argument("--region", default="garage", help="S3 Region (default: garage)")
    parser.add_argument("--dry-run", action="store_true", help="List actions without uploading")

    args = parser.parse_args()
    restore_backup(
        tar_path=args.backup_file,
        endpoint_url=args.endpoint,
        bucket=args.bucket,
        access_key=args.access_key,
        secret_key=args.secret_key,
        region=args.region,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
