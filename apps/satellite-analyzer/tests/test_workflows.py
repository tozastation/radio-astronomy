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
        async def mock_decode(wav_path: str) -> dict:
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
