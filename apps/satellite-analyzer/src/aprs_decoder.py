from dataclasses import dataclass, asdict
import os
import datetime
import logging
import re
import shutil
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class APRSFrame:
    timestamp: str
    source: str
    destination: str
    repeater: str
    message: str
    raw_frame: str

    def to_dict(self):
        return asdict(self)


class APRSDecoder:
    """Dire Wolf の公式オフライン WAV デコードツール atest による 1200bps AFSK / AX.25 APRS パケットのデコーダー"""

    def __init__(self, binary_path: Optional[str] = None, direwolf_binary: Optional[str] = None):
        # atest を優先し、明示指定または direwolf 指定時もフォールバック
        candidate = binary_path or direwolf_binary or os.getenv("ATEST_BINARY") or "atest"
        if not shutil.which(candidate) and shutil.which("direwolf"):
            # direwolf しか見つからない場合でも、同一ディレクトリ内の atest を探す
            direwolf_path = shutil.which("direwolf")
            atest_candidate = os.path.join(os.path.dirname(direwolf_path), "atest")
            if os.path.exists(atest_candidate):
                candidate = atest_candidate

        self.binary_path = candidate
        self.has_binary = shutil.which(self.binary_path) is not None
        # 後方互換性エイリアス
        self.has_direwolf = self.has_binary
        self.direwolf_binary = self.binary_path

    def decode_wav(self, wav_path: str) -> List[APRSFrame]:
        """WAV ファイルから APRS パケットをデコードする (atest -B 1200 <wav>)"""
        if not self.has_binary:
            logger.warning(
                f"atest command ('{self.binary_path}') not found on system. Skipping APRS decoding for {wav_path}."
            )
            return []

        try:
            # atest は WAV ファイルから直接オフラインデコードを行う公式ツール
            # -B 1200: 1200 baud AFSK (Bell 202: 1200Hz Mark / 2200Hz Space)
            cmd = [
                self.binary_path,
                "-B", "1200",
                wav_path,
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return self.parse_atest_output(result.stdout)
        except Exception as e:
            logger.error(f"Error running atest on {wav_path}: {e}")
            return []

    def parse_atest_output(self, output: str) -> List[APRSFrame]:
        """atest の標準出力テキストから AX.25 / APRS パケットを正規表現パースする"""
        frames: List[APRSFrame] = []
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # パターン: [0.1] JA1XXX>CQ,ARISS*::Hello from Tokyo via ISS! または SOURCE>DEST,PATH:MESSAGE
        pattern = re.compile(
            r"(?:\[[\d\.]+\]\s*)?([A-Z0-9\-_]+)>([A-Z0-9\-_]+)(?:,([A-Z0-9\-_*]+))?(?::|::)(.*)"
        )

        ignore_prefixes = (
            "Dire Wolf",
            "Audio",
            "Channel",
            "Fix Bits",
            "Reading",
            "0 packets",
            "1 packets",
            "DECODED",
            "samples per",
            "audio bytes",
        )

        for line in output.splitlines():
            line = line.strip()
            if not line or any(line.startswith(p) for p in ignore_prefixes) or "packets decoded in" in line:
                continue

            match = pattern.search(line)
            if match:
                src = match.group(1).strip()
                dst = match.group(2).strip()
                rep = (match.group(3) or "").strip().rstrip("*")
                raw_msg = match.group(4).strip()
                # Dire Wolf の制御文字エスケープ (<0x0a> 等) を除去
                msg = re.sub(r"<0x[0-9a-fA-F]{2}>", "", raw_msg).strip()

                frame = APRSFrame(
                    timestamp=now_iso,
                    source=src,
                    destination=dst,
                    repeater=rep,
                    message=msg,
                    raw_frame=line,
                )
                frames.append(frame)

        return frames

    # 後方互換性メソッドエイリアス
    parse_direwolf_output = parse_atest_output
