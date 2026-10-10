from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Any, Optional
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import (
        download_recording_activity,
        decode_packets_activity,
        generate_spectrogram_activity,
        save_results_activity,
        cleanup_raw_recording_activity,
    )


@dataclass
class PassAnalysisParams:
    s3_key: str
    satellite: str
    pass_id: str
    bucket_name: str = "satellite-recordings"
    keep_raw: bool = False


@workflow.defn
class AnalyzeSatellitePassWorkflow:
    """衛星通過時の録音データを解析し、成果物を保存してクリーンアップするメインワークフロー"""

    @workflow.run
    async def run(self, params: PassAnalysisParams) -> Dict[str, Any]:
        workflow.logger.info(
            f"Starting AnalyzeSatellitePassWorkflow for {params.satellite} ({params.pass_id})"
        )

        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=2),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=30),
            maximum_attempts=3,
        )

        # 1. WAV ダウンロード
        local_wav_path = await workflow.execute_activity(
            download_recording_activity,
            params.s3_key,
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=retry_policy,
        )

        # 2. APRS パケットデコード
        packets_data = await workflow.execute_activity(
            decode_packets_activity,
            local_wav_path,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        # 3. スペクトログラム画像生成
        spectrogram_png_path = await workflow.execute_activity(
            generate_spectrogram_activity,
            local_wav_path,
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=retry_policy,
        )

        # 4. S3 へ解析結果保存
        save_params = {
            "satellite": params.satellite,
            "pass_id": params.pass_id,
            "packets_data": packets_data,
            "spectrogram_png_path": spectrogram_png_path,
        }
        await workflow.execute_activity(
            save_results_activity,
            save_params,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy,
        )

        # 5. 元 WAV ファイルの削除 (ストレージクリーンアップ)
        if not params.keep_raw:
            await workflow.execute_activity(
                cleanup_raw_recording_activity,
                params.s3_key,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy,
            )

        workflow.logger.info(
            f"Successfully finished AnalyzeSatellitePassWorkflow for {params.satellite} ({params.pass_id})"
        )

        return {
            "status": "completed",
            "satellite": params.satellite,
            "pass_id": params.pass_id,
            "packets_count": packets_data.get("packets_count", 0),
        }
