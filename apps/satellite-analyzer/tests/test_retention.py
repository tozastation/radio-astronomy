import pytest
from datetime import datetime, timezone, timedelta
from retention import archive_and_rotate_raw_recording, rotate_satellite_archive, preserve_golden_raw_recording


class FakeS3Client:
    def __init__(self):
        # key -> {"data": bytes, "LastModified": datetime, "Size": int}
        self.objects = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes = b"data", LastModified: datetime = None):
        if LastModified is None:
            LastModified = datetime.now(timezone.utc)
        self.objects[Key] = {
            "Body": Body,
            "LastModified": LastModified,
            "Size": len(Body),
        }

    def copy_object(self, Bucket: str, CopySource: dict, Key: str):
        src_key = CopySource["Key"]
        if src_key not in self.objects:
            raise KeyError(f"Source key not found: {src_key}")
        src = self.objects[src_key]
        self.objects[Key] = {
            "Body": src["Body"],
            "LastModified": datetime.now(timezone.utc),
            "Size": src["Size"],
        }

    def delete_object(self, Bucket: str, Key: str):
        self.objects.pop(Key, None)

    def list_objects_v2(self, Bucket: str, Prefix: str):
        matched = []
        for k, v in self.objects.items():
            if k.startswith(Prefix):
                matched.append({
                    "Key": k,
                    "Size": v["Size"],
                    "LastModified": v["LastModified"],
                })
        return {"Contents": matched}


def test_archive_and_rotate_moves_raw_to_archive():
    s3 = FakeS3Client()
    s3.put_object("satellite-recordings", "raw/ISS/pass_1.wav", b"dummy_audio_data")

    res = archive_and_rotate_raw_recording(
        s3_client=s3,
        bucket="satellite-recordings",
        raw_s3_key="raw/ISS/pass_1.wav",
        satellite="ISS",
        max_versions=3,
    )

    assert res == "archive/ISS/pass_1.wav"
    assert "raw/ISS/pass_1.wav" not in s3.objects
    assert "archive/ISS/pass_1.wav" in s3.objects


def test_rotate_version_limit():
    s3 = FakeS3Client()
    now = datetime.now(timezone.utc)

    # 4世代作成 (古い順: v1, v2, v3, v4)
    for i in range(1, 5):
        s3.put_object(
            "satellite-recordings",
            f"archive/METEOR/pass_{i}.u8",
            Body=b"sample" * 10,
            LastModified=now + timedelta(seconds=i * 10),
        )

    assert len(s3.objects) == 4

    deleted = rotate_satellite_archive(
        s3_client=s3,
        bucket="satellite-recordings",
        satellite="METEOR",
        max_versions=3,
        max_total_bytes=1024 * 1024,
    )

    assert deleted == ["archive/METEOR/pass_1.u8"]
    assert "archive/METEOR/pass_1.u8" not in s3.objects
    assert "archive/METEOR/pass_2.u8" in s3.objects
    assert "archive/METEOR/pass_3.u8" in s3.objects
    assert "archive/METEOR/pass_4.u8" in s3.objects


def test_rotate_size_limit():
    s3 = FakeS3Client()
    now = datetime.now(timezone.utc)

    # 各 100 バイトで 3世代作成 (合計 300 バイト)
    for i in range(1, 4):
        s3.put_object(
            "satellite-recordings",
            f"archive/AO-73/pass_{i}.wav",
            Body=b"x" * 100,
            LastModified=now + timedelta(seconds=i * 10),
        )

    # 容量上限を 150 バイトに設定 (pass_1, pass_2 が削除され pass_3 のみ残るべき)
    deleted = rotate_satellite_archive(
        s3_client=s3,
        bucket="satellite-recordings",
        satellite="AO-73",
        max_versions=5,
        max_total_bytes=150,
    )

    assert "archive/AO-73/pass_1.wav" in deleted
    assert "archive/AO-73/pass_2.wav" in deleted
    assert "archive/AO-73/pass_3.wav" in s3.objects
    assert len(s3.objects) == 1


def test_preserve_golden_raw_recording():
    s3 = FakeS3Client()
    s3.put_object("satellite-recordings", "raw/METEOR/pass_golden.raw", b"golden_raw_iq_bytes")

    golden_key = preserve_golden_raw_recording(
        s3_client=s3,
        bucket="satellite-recordings",
        source_s3_key="raw/METEOR/pass_golden.raw",
        satellite="METEOR",
    )

    assert golden_key == "golden/METEOR/pass_golden.raw"
    assert "golden/METEOR/pass_golden.raw" in s3.objects
    assert s3.objects["golden/METEOR/pass_golden.raw"]["Body"] == b"golden_raw_iq_bytes"

