import os
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


def preserve_golden_raw_recording(
    s3_client,
    bucket: str,
    source_s3_key: str,
    satellite: str,
) -> Optional[str]:
    """
    気象画像やテレメトリの復調に成功した貴重な生データ（RAW/WAV）を、
    ローテーション対象外の永久保護領域 golden/{satellite}/{filename} へコピー退避する。
    """
    filename = os.path.basename(source_s3_key)
    golden_key = f"golden/{satellite}/{filename}"

    try:
        logger.info(f"Preserving golden raw recording: s3://{bucket}/{source_s3_key} to s3://{bucket}/{golden_key}...")
        s3_client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": source_s3_key},
            Key=golden_key,
        )
        return golden_key
    except Exception as e:
        logger.error(f"Failed to preserve golden raw recording {source_s3_key}: {e}")
        return None


def archive_and_rotate_raw_recording(
    s3_client,
    bucket: str,
    raw_s3_key: str,
    satellite: str,
    max_versions: int = 3,
    max_total_bytes: int = 1024 * 1024 * 1024,  # デフォルト 1 GB
) -> Optional[str]:
    """
    未処理 raw 録音ファイルを archive/{satellite}/{filename} へ退避移動し、
    指定されたバージョン数・容量閾値に基づいて古い録音を自動削除（FIFO ローテーション）する。

    Args:
        s3_client: boto3 S3 クライアント
        bucket: バケット名 (satellite-recordings)
        raw_s3_key: 移動対象の S3 キー (例: raw/ISS (ZARYA)/ISS_123.wav)
        satellite: 衛星名 (例: ISS (ZARYA))
        max_versions: 衛星ごとに保持する最大バージョン数 (デフォルト 3)
        max_total_bytes: アーカイブ許容最大バイト数 (デフォルト 1GB)

    Returns:
        移動先の archive S3 キー (例: archive/ISS (ZARYA)/ISS_123.wav)
    """
    filename = os.path.basename(raw_s3_key)
    archive_key = f"archive/{satellite}/{filename}"

    try:
        # 1. raw から archive へのコピー
        logger.info(f"Archiving s3://{bucket}/{raw_s3_key} to s3://{bucket}/{archive_key}...")
        s3_client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": raw_s3_key},
            Key=archive_key,
        )

        # 2. 元の raw オブジェクトを削除 (KEDA が未処理タスク 0 と認識できるようにする)
        s3_client.delete_object(Bucket=bucket, Key=raw_s3_key)
        logger.info(f"Deleted source raw key: s3://{bucket}/{raw_s3_key}")

    except Exception as e:
        logger.error(f"Failed to copy/move raw object to archive: {e}")
        # 移動に失敗した場合は安全のため削除を試みる
        try:
            s3_client.delete_object(Bucket=bucket, Key=raw_s3_key)
        except Exception:
            pass
        return None

    # 3. archive/{satellite}/ 配下の世代管理 & 容量ローテーション
    try:
        rotate_satellite_archive(
            s3_client=s3_client,
            bucket=bucket,
            satellite=satellite,
            max_versions=max_versions,
            max_total_bytes=max_total_bytes,
        )
    except Exception as e:
        logger.warning(f"Error during archive rotation for {satellite}: {e}")

    return archive_key


def rotate_satellite_archive(
    s3_client,
    bucket: str,
    satellite: str,
    max_versions: int = 3,
    max_total_bytes: int = 1024 * 1024 * 1024,
) -> List[str]:
    """
    archive/{satellite}/ 配下のオブジェクトを LastModified 昇順（古い順）に評価し、
    世代数および容量制限を超過した古いオブジェクトを削除する。

    Returns:
        削除された S3 キーのリスト
    """
    prefix = f"archive/{satellite}/"
    resp = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    contents = resp.get("Contents", [])

    if not contents:
        return []

    # LastModified 昇順 (古い順) にソート
    sorted_items = sorted(contents, key=lambda x: x.get("LastModified", 0))
    deleted_keys = []

    # A. 世代数制限チェック (最新 max_versions 件を残し、古いものを削除対象へ)
    if len(sorted_items) > max_versions:
        excess_count = len(sorted_items) - max_versions
        to_delete = sorted_items[:excess_count]
        remaining = sorted_items[excess_count:]

        for item in to_delete:
            key = item["Key"]
            logger.info(f"Rotation (version limit): Deleting old raw archive s3://{bucket}/{key}")
            s3_client.delete_object(Bucket=bucket, Key=key)
            deleted_keys.append(key)
    else:
        remaining = sorted_items

    # B. 容量制限チェック (remaining の合計サイズが max_total_bytes を下回るまで古い順に削除)
    current_total_bytes = sum(item.get("Size", 0) for item in remaining)
    while current_total_bytes > max_total_bytes and len(remaining) > 1:
        oldest = remaining.pop(0)
        key = oldest["Key"]
        size = oldest.get("Size", 0)
        logger.info(
            f"Rotation (byte limit exceeded {current_total_bytes} > {max_total_bytes}): "
            f"Deleting s3://{bucket}/{key} ({size} bytes)"
        )
        s3_client.delete_object(Bucket=bucket, Key=key)
        deleted_keys.append(key)
        current_total_bytes -= size

    return deleted_keys
