import os
import pytest
from unittest.mock import MagicMock, patch
from s3_uploader import S3Uploader


def test_s3_uploader_success(tmp_path):
    """アップロード成功時に S3 に格納され、ローカルファイルが削除されることの検証"""
    dummy_file = tmp_path / "test_pass.wav"
    dummy_file.write_bytes(b"RIFFdummydataWAVE")
    assert dummy_file.exists()

    mock_s3_client = MagicMock()

    with patch("boto3.client", return_value=mock_s3_client):
        uploader = S3Uploader(
            endpoint_url="http://garage-s3.storage.svc:3900",
            access_key="dummy-key",
            secret_key="dummy-secret",
            bucket_name="satellite-recordings",
        )

        success = uploader.upload_and_cleanup(
            local_file_path=str(dummy_file),
            s3_key="raw/ISS/test_pass.wav",
        )

        assert success is True
        mock_s3_client.upload_file.assert_called_once_with(
            str(dummy_file),
            "satellite-recordings",
            "raw/ISS/test_pass.wav",
        )
        # アップロード成功後はローカルファイルが削除されていること
        assert not dummy_file.exists()


def test_s3_uploader_failure_preserves_local_file(tmp_path):
    """アップロード失敗（ネットワーク切断等）時はローカルファイルが保持されることの検証"""
    dummy_file = tmp_path / "failed_pass.wav"
    dummy_file.write_bytes(b"important data")

    mock_s3_client = MagicMock()
    mock_s3_client.upload_file.side_effect = Exception("Connection refused")

    with patch("boto3.client", return_value=mock_s3_client):
        uploader = S3Uploader(
            endpoint_url="http://garage-s3.storage.svc:3900",
            access_key="dummy-key",
            secret_key="dummy-secret",
            bucket_name="satellite-recordings",
        )

        success = uploader.upload_and_cleanup(
            local_file_path=str(dummy_file),
            s3_key="raw/ISS/failed_pass.wav",
        )

        assert success is False
        # 失敗時はローカルファイルが削除されずに残っていること
        assert dummy_file.exists()
