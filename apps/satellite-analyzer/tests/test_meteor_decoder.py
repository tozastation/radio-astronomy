import os
import pytest
from meteor_decoder import MeteorDecoder, MeteorDecodeResult


def test_meteor_decoder_satdump_not_found():
    decoder = MeteorDecoder(binary_path="/nonexistent/satdump")
    result = decoder.decode_baseband(
        input_path="/tmp/fake.u8",
        output_dir="/tmp/out",
    )
    assert result.status == "satdump_not_found"
    assert len(result.images) == 0
    assert len(result.cadu_files) == 0


def test_meteor_decode_result_to_dict():
    res = MeteorDecodeResult(
        satellite="METEOR-M2 4",
        pipeline="meteor_m2-x_lrpt_80k",
        status="success",
        images=["/tmp/msu_mr.png"],
        cadu_files=["/tmp/meteor.cadu"],
        summary_text="復調成功",
    )
    d = res.to_dict()
    assert d["satellite"] == "METEOR-M2 4"
    assert d["status"] == "success"
    assert len(d["images"]) == 1


def test_meteor_decoder_with_golden_cadu(tmp_path):
    cadu_asset = os.path.join(os.path.dirname(__file__), "../assets/meteor_m2_sample.cadu")
    if not os.path.exists(cadu_asset):
        pytest.skip("Golden CADU sample not found")

    decoder = MeteorDecoder()
    if not decoder.has_binary:
        pytest.skip("SatDump binary not found")

    out_dir = str(tmp_path / "satdump_test_out")
    result = decoder.decode_baseband(
        input_path=cadu_asset,
        output_dir=out_dir,
    )

    assert result.status == "success"
    assert len(result.images) > 0
    assert "気象画像復調成功" in result.summary_text
    # 実際に PNG が存在することを確認
    assert any(img.endswith(".png") for img in result.images)


def test_meteor_decoder_falls_back_between_72k_and_80k(tmp_path, monkeypatch):
    """生IQ (.raw) の復調時に 72k (meteor_m2-x_lrpt) と 80k (meteor_m2-x_lrpt_80k) の両パイプラインを試行することの検証"""
    raw_file = tmp_path / "pass.raw"
    raw_file.write_bytes(b"\x80\x80" * 2048)
    out_dir = tmp_path / "out"

    called_pipelines = []

    def fake_run(cmd, capture_output, text, timeout):
        pipeline_name = cmd[1]
        target_out_dir = cmd[4]
        called_pipelines.append(pipeline_name)
        os.makedirs(target_out_dir, exist_ok=True)
        # 1回目の 72k では画像が生成されず、2回目の 80k で画像が生成されるケースを模擬
        if pipeline_name == "meteor_m2-x_lrpt_80k":
            img_path = os.path.join(target_out_dir, "lrpt_msu_mr.png")
            with open(img_path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        class DummyCompletedProcess:
            returncode = 0
            stdout = ""
            stderr = ""

        return DummyCompletedProcess()

    monkeypatch.setattr("subprocess.run", fake_run)
    decoder = MeteorDecoder(binary_path="python3")
    res = decoder.decode_baseband(str(raw_file), str(out_dir))

    assert called_pipelines == ["meteor_m2-x_lrpt", "meteor_m2-x_lrpt_80k"]
    assert res.status == "success"
    assert res.pipeline == "meteor_m2-x_lrpt_80k"
    assert len(res.images) == 1

