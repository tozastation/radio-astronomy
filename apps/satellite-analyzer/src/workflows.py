from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Any, Optional
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import (
        analyze_satellite_pass_activity,
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

        activity_params = {
            "s3_key": params.s3_key,
            "satellite": params.satellite,
            "pass_id": params.pass_id,
            "keep_raw": params.keep_raw,
        }

        # 統合アクティビティで不可分に完走（分散環境でのローカルパス共有問題を根絶）
        result = await workflow.execute_activity(
            analyze_satellite_pass_activity,
            activity_params,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )

        workflow.logger.info(
            f"Successfully finished AnalyzeSatellitePassWorkflow for {params.satellite} ({params.pass_id})"
        )

        return result
