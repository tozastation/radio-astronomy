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
