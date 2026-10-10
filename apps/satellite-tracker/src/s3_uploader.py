import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


class S3Uploader:
    """Garage S3 への非同期アップロードおよびローカル一時ファイル削除を担うアップローダー"""

    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket_name: Optional[str] = None,
        region_name: str = "garage",
    ):
        self.endpoint_url = endpoint_url or os.getenv("GARAGE_S3_ENDPOINT", "http://garage-s3.storage.svc.cluster.local:3900")
        self.access_key = access_key or os.getenv("GARAGE_ACCESS_KEY", "")
        self.secret_key = secret_key or os.getenv("GARAGE_SECRET_KEY", "")
        self.bucket_name = bucket_name or os.getenv("GARAGE_BUCKET", "satellite-recordings")
        self.region_name = region_name
        self._s3_client = None

    def _get_client(self):
        if self._s3_client is None:
            if not HAS_BOTO3:
                raise RuntimeError("boto3 is required for S3 upload but not installed.")
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region_name,
            )
        return self._s3_client

    def upload_and_cleanup(self, local_file_path: str, s3_key: str) -> bool:
        """ファイルを S3 にアップロードし、成功時にローカルファイルを安全に削除する"""
        if not os.path.exists(local_file_path):
            logger.warning(f"S3Uploader: File not found: {local_file_path}")
            return False

        try:
            client = self._get_client()
            logger.info(f"S3Uploader: Uploading {local_file_path} to s3://{self.bucket_name}/{s3_key}...")
            client.upload_file(local_file_path, self.bucket_name, s3_key)
            logger.info(f"S3Uploader: Successfully uploaded {s3_key}")

            # アップロード成功後のローカルクリーンアップ
            try:
                os.remove(local_file_path)
                logger.info(f"S3Uploader: Cleaned up local file: {local_file_path}")
            except Exception as e:
                logger.warning(f"S3Uploader: Could not delete local file {local_file_path}: {e}")

            return True
        except Exception as e:
            logger.error(f"S3Uploader: Upload failed for {local_file_path} ({e}). Local file preserved.")
            return False
