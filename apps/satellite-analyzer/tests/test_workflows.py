import pytest
from dataclasses import dataclass
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflows import AnalyzeSatellitePassWorkflow, PassAnalysisParams
from activities import (
    download_recording_activity,
    decode_packets_activity,
    generate_spectrogram_activity,
    save_results_activity,
    cleanup_raw_recording_activity,
)


@pytest.mark.asyncio
async def test_satellite_analysis_workflow_execution():
    """AnalyzeSatellitePassWorkflow のエンドツーエンド実行テスト"""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        task_queue = "test-satellite-analysis"

        from temporalio import activity

        # モック Activity の設定
        @activity.defn(name="download_recording_activity")
        async def mock_download(s3_key: str) -> str:
            return "/tmp/mock_pass.wav"

        @activity.defn(name="decode_packets_activity")
        async def mock_decode(wav_path: str, satellite: str = "") -> dict:
            return {
                "packets_count": 1,
                "packets": [{"source": "JA1XXX", "message": "Hello via ISS"}],
            }

        @activity.defn(name="generate_spectrogram_activity")
        async def mock_spectrogram(wav_path: str) -> str:
            return "/tmp/mock_spectrogram.png"

        @activity.defn(name="save_results_activity")
        async def mock_save(params: dict) -> bool:
            return True

        @activity.defn(name="cleanup_raw_recording_activity")
        async def mock_cleanup(s3_key: str) -> bool:
            return True

        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[AnalyzeSatellitePassWorkflow],
            activities=[
                mock_download,
                mock_decode,
                mock_spectrogram,
                mock_save,
                mock_cleanup,
            ],
        ):
            params = PassAnalysisParams(
                s3_key="raw/ISS/test_pass.wav",
                satellite="ISS",
                pass_id="ISS_20261010_090048",
                bucket_name="satellite-recordings",
            )

            result = await env.client.execute_workflow(
                AnalyzeSatellitePassWorkflow.run,
                params,
                id="test-workflow-001",
                task_queue=task_queue,
            )

            assert result["status"] == "completed"
            assert result["satellite"] == "ISS"
            assert result["packets_count"] == 1


@pytest.mark.asyncio
async def test_decode_packets_activity_multisatellite(tmp_path):
    """decode_packets_activity が衛星種別 (ISS, METEOR, FUNCUBE) に応じたメタデータを返すことの検証"""
    from activities import decode_packets_activity, classify_satellite

    # 1. ISS (APRS)
    iss_info = classify_satellite("ISS (ZARYA)")
    assert iss_info["category"] == "amateur_packet"
    assert "145.825" in iss_info["frequency_label"]
    assert iss_info["display_icon"] == "🚀"

    # 2. METEOR-M2 4 (Weather)
    meteor_info = classify_satellite("METEOR-M2 4")
    assert meteor_info["category"] == "weather_lrpt"
    assert "137.900" in meteor_info["frequency_label"]
    assert meteor_info["display_icon"] == "🛰️"

    # 3. FUNCUBE-1 (CubeSat)
    fc_info = classify_satellite("FUNCUBE-1 (AO-73)")
    assert fc_info["category"] == "cubesat_telemetry"
    assert "145.935" in fc_info["frequency_label"]
    assert fc_info["display_icon"] == "📻"

    # 4. Activity 実行 (METEOR と FUNCUBE は専用サマリを返す)
    dummy_wav = tmp_path / "dummy.wav"
    dummy_wav.write_bytes(b"RIFF....WAVEfmt ....data....")

    res_meteor = await decode_packets_activity(str(dummy_wav), "METEOR-M2 4")
    assert res_meteor["satellite_type"] == "WeatherSatellite"
    assert "LRPT" in res_meteor["signal_type"]
    assert res_meteor["packets_count"] == 0

    res_fc = await decode_packets_activity(str(dummy_wav), "FUNCUBE-1 (AO-73)")
    assert res_fc["satellite_type"] == "CubeSat"
    assert "BPSK" in res_fc["signal_type"]
    assert res_fc["packets_count"] == 0
