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

    def decode_baseband(
        self,
        input_path: str,
        output_dir: str,
        pipeline: str = "meteor_m2-x_lrpt_80k",
        samplerate: int = 240000,
        baseband_format: str = "cu8",
        timeout: int = 60,
    ) -> MeteorDecodeResult:
        """生 IQ ベースバンドファイル (cu8 / u8) から SatDump で MSU-MR 気象画像・CADU を復調・生成する"""
        if not self.has_binary:
            logger.warning(
                f"SatDump command ('{self.binary_path}') not found. Skipping METEOR decoding for {input_path}."
            )
            return MeteorDecodeResult(
                satellite="METEOR-M2",
                pipeline=pipeline,
                status="satdump_not_found",
                images=[],
                cadu_files=[],
                summary_text="SatDump がシステムに未導入のためスキップされました",
            )

        if not os.path.exists(input_path) or os.path.getsize(input_path) < 1024:
            logger.warning(f"Input file too small or missing for SatDump: {input_path}")
            return MeteorDecodeResult(
                satellite="METEOR-M2",
                pipeline=pipeline,
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
                    "METEOR-M2 4\n"
                    "1 59051U 24039A   26284.50000000  .00000000  00000-0  50000-4 0  9999\n"
                    "2 59051  98.7000 120.0000 0001000  90.0000 270.0000 14.20000000 30000\n"
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

        try:
            cmd = [
                self.binary_path,
                pipeline,
                "baseband",
                input_path,
                output_dir,
                "--samplerate", str(samplerate),
                "--baseband_format", baseband_format,
            ]
            logger.info(f"Running SatDump: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            # 生成された成果物の探索
            images = []
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
                images.extend(glob.glob(os.path.join(output_dir, "**", ext), recursive=True))

            cadu_files = glob.glob(os.path.join(output_dir, "**", "*.cadu"), recursive=True)
            non_empty_cadus = [c for c in cadu_files if os.path.exists(c) and os.path.getsize(c) > 0]

            if images:
                status = "success"
                summary_text = f"METEOR-M2 LRPT 気象画像復調成功 ({len(images)} 枚の画像を生成)"
            elif non_empty_cadus:
                status = "cadu_only"
                summary_text = f"METEOR-M2 CADU フレーム検出完了 ({len(non_empty_cadus)} ファイル)"
            else:
                status = "no_signal"
                summary_text = "METEOR-M2 信号微弱 (QPSK シンボル同期未確立)"

            return MeteorDecodeResult(
                satellite="METEOR-M2",
                pipeline=pipeline,
                status=status,
                images=images,
                cadu_files=non_empty_cadus,
                summary_text=summary_text,
            )

        except subprocess.TimeoutExpired:
            logger.error(f"SatDump timed out ({timeout}s) on {input_path}")
            return MeteorDecodeResult(
                satellite="METEOR-M2",
                pipeline=pipeline,
                status="timeout",
                images=[],
                cadu_files=[],
                summary_text=f"SatDump 処理がタイムアウトしました ({timeout}秒)",
            )
        except Exception as e:
            logger.error(f"Error running SatDump on {input_path}: {e}")
            return MeteorDecodeResult(
                satellite="METEOR-M2",
                pipeline=pipeline,
                status="error",
                images=[],
                cadu_files=[],
                summary_text=f"SatDump 実行エラー: {e}",
            )
