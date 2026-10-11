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
