from dataclasses import dataclass, asdict
import os
import glob
import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class MeteorDecodeResult:
    satellite: str
    pipeline: str
    status: str
    images: List[str]
    cadu_files: List[str]
    summary_text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MeteorDecoder:
    """SatDump CLI による METEOR-M2 LRPT (QPSK 72k/80k) 気象衛星画像・CADU デコーダー"""

    def __init__(self, binary_path: Optional[str] = None):
        candidate = binary_path or os.getenv("SATDUMP_BINARY") or "satdump"
        self.binary_path = candidate
        self.has_binary = shutil.which(self.binary_path) is not None

    DEFAULT_PIPELINES = ("meteor_m2-x_lrpt", "meteor_m2-x_lrpt_80k")

    def decode_baseband(
        self,
        input_path: str,
        output_dir: str,
        pipeline: Optional[str] = None,
        samplerate: int = 240000,
        baseband_format: str = "cu8",
        timeout: int = 120,
    ) -> MeteorDecodeResult:
        """生 IQ ベースバンドファイル (cu8 / u8) から SatDump で MSU-MR 気象画像・CADU を復調・生成する。
        pipeline 未指定時は標準運用の 72k (meteor_m2-x_lrpt) と 80k (meteor_m2-x_lrpt_80k) を順に試行する。
        """
        candidate_pipelines = [pipeline] if pipeline else list(self.DEFAULT_PIPELINES)
        primary_pipeline = candidate_pipelines[0]

        if not self.has_binary:
            logger.warning(
                f"SatDump command ('{self.binary_path}') not found. Skipping METEOR decoding for {input_path}."
            )
            return MeteorDecodeResult(
                satellite="METEOR-M2",
                pipeline=primary_pipeline,
                status="satdump_not_found",
                images=[],
                cadu_files=[],
                summary_text="SatDump がシステムに未導入のためスキップされました",
            )

        if not os.path.exists(input_path) or os.path.getsize(input_path) < 1024:
            logger.warning(f"Input file too small or missing for SatDump: {input_path}")
            return MeteorDecodeResult(
                satellite="METEOR-M2",
                pipeline=primary_pipeline,
                status="no_signal",
                images=[],
                cadu_files=[],
                summary_text="METEOR-M2 信号微弱 (有効なIQデータなし)",
            )

        os.makedirs(output_dir, exist_ok=True)

        # 外部ネットワーク (celestrak.org) への TLE ダウンロード試行によるタイムアウトを防止
        satdump_cfg_dir = os.path.expanduser("~/.config/satdump")
        satdump_tle_path = os.path.join(satdump_cfg_dir, "satdump_tles.txt")
        satdump_settings_path = os.path.join(satdump_cfg_dir, "settings.json")
        try:
            os.makedirs(satdump_cfg_dir, exist_ok=True)
            if not os.path.exists(satdump_tle_path):
                default_tle_content = (
                    "METEOR-M2 3\n"
                    "1 57166U 23091A   26296.25648911 -.00000011  00000+0  13974-4 0  9991\n"
                    "2 57166  98.5934 348.5131 0004559 145.3247 214.8230 14.24054780172745\n"
                    "METEOR-M2 4\n"
                    "1 59051U 24039A   26296.27566674  .00000005  00000+0  21993-4 0  9998\n"
                    "2 59051  98.7198 254.0636 0007418 151.3257 208.8330 14.22441005137473\n"
                )
                with open(satdump_tle_path, "w", encoding="utf-8") as f:
                    f.write(default_tle_content)

            # TLE 自動更新を確実に Never に設定して外部ネットワーク接続待ちを完全に抑止
            settings_data = {
                "user_interface": {
                    "tle_update_interval": {
                        "value": "Never"
                    }
                },
                "user": {
                    "tles_last_updated": 2147483647
                }
            }
            import json
            with open(satdump_settings_path, "w", encoding="utf-8") as f:
                json.dump(settings_data, f, indent=4)
        except Exception as e:
            logger.warning(f"Could not initialize satdump settings/TLEs: {e}")

        best_result: Optional[MeteorDecodeResult] = None
        for idx, pipe in enumerate(candidate_pipelines):
            pipe_out_dir = output_dir if len(candidate_pipelines) == 1 else os.path.join(output_dir, pipe)
            os.makedirs(pipe_out_dir, exist_ok=True)
            try:
                if input_path.lower().endswith(".cadu"):
                    cmd = [
                        self.binary_path,
                        pipe,
                        "cadu",
                        input_path,
                        pipe_out_dir,
                    ]
                else:
                    cmd = [
                        self.binary_path,
                        pipe,
                        "baseband",
                        input_path,
                        pipe_out_dir,
                        "--samplerate", str(samplerate),
                        "--baseband_format", baseband_format,
                        "--dc_block", "true",
                    ]
                logger.info(f"Running SatDump ({idx + 1}/{len(candidate_pipelines)}): {' '.join(cmd)}")
                subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )

                images = []
                for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
                    for candidate_img in glob.glob(os.path.join(pipe_out_dir, "**", ext), recursive=True):
                        if os.path.exists(candidate_img) and os.path.getsize(candidate_img) > 1024:
                            images.append(candidate_img)

                cadu_files = glob.glob(os.path.join(pipe_out_dir, "**", "*.cadu"), recursive=True)
                non_empty_cadus = [c for c in cadu_files if os.path.exists(c) and os.path.getsize(c) > 0]

                if images:
                    return MeteorDecodeResult(
                        satellite="METEOR-M2",
                        pipeline=pipe,
                        status="success",
                        images=images,
                        cadu_files=non_empty_cadus,
                        summary_text=f"METEOR-M2 LRPT ({pipe}) 気象画像復調成功 ({len(images)} 枚の画像を生成)",
                    )
                elif non_empty_cadus:
                    candidate_res = MeteorDecodeResult(
                        satellite="METEOR-M2",
                        pipeline=pipe,
                        status="cadu_only",
                        images=[],
                        cadu_files=non_empty_cadus,
                        summary_text=f"METEOR-M2 CADU フレーム検出完了 ({len(non_empty_cadus)} ファイル)",
                    )
                    if best_result is None or best_result.status != "cadu_only":
                        best_result = candidate_res
                else:
                    if best_result is None:
                        best_result = MeteorDecodeResult(
                            satellite="METEOR-M2",
                            pipeline=pipe,
                            status="no_signal",
                            images=[],
                            cadu_files=[],
                            summary_text="METEOR-M2 信号微弱 (QPSK シンボル同期未確立)",
                        )
            except subprocess.TimeoutExpired:
                logger.error(f"SatDump ({pipe}) timed out ({timeout}s) on {input_path}")
                if best_result is None:
                    best_result = MeteorDecodeResult(
                        satellite="METEOR-M2",
                        pipeline=pipe,
                        status="timeout",
                        images=[],
                        cadu_files=[],
                        summary_text=f"SatDump 処理がタイムアウトしました ({timeout}秒)",
                    )
            except Exception as e:
                logger.error(f"Error running SatDump ({pipe}) on {input_path}: {e}")
                if best_result is None:
                    best_result = MeteorDecodeResult(
                        satellite="METEOR-M2",
                        pipeline=pipe,
                        status="error",
                        images=[],
                        cadu_files=[],
                        summary_text=f"SatDump 実行エラー: {e}",
                    )

        assert best_result is not None
        return best_result
