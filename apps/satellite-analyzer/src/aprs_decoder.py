from dataclasses import dataclass, asdict
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
    """direwolf による 1200bps AFSK / AX.25 APRS パケットのデコーダー"""

    def __init__(self, direwolf_binary: str = "direwolf"):
        self.direwolf_binary = direwolf_binary
        self.has_direwolf = shutil.which(direwolf_binary) is not None

    def decode_wav(self, wav_path: str) -> List[APRSFrame]:
        """WAV ファイルから APRS パケットをデコードする"""
        if not self.has_direwolf:
            logger.warning(
                f"direwolf command not found on system. Skipping APRS decoding for {wav_path}."
            )
            return []

        try:
            # direwolf -r 48000 -c /dev/null -q h -d a -t 0 -r 48000 -B 1200 <wav>
            cmd = [
                self.direwolf_binary,
                "-r", "48000",
                "-B", "1200",
                "-d", "a",
                "-q", "h",
                "-t", "0",
                "-c", "/dev/null",
                wav_path,
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return self.parse_direwolf_output(result.stdout)
        except Exception as e:
            logger.error(f"Error running direwolf on {wav_path}: {e}")
            return []

    def parse_direwolf_output(self, output: str) -> List[APRSFrame]:
        """direwolf の標準出力テキストから AX.25 / APRS パケットを正規表現パースする"""
        frames: List[APRSFrame] = []
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # パターン: [0.1] JA1XXX>CQ,ARISS*::Hello from Tokyo via ISS! または SOURCE>DEST,PATH:MESSAGE
        pattern = re.compile(
            r"(?:\[[\d\.]+\]\s*)?([A-Z0-9\-_]+)>([A-Z0-9\-_]+)(?:,([A-Z0-9\-_*]+))?(?::|::)(.*)"
        )

        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("Dire Wolf") or line.startswith("Audio"):
                continue

            match = pattern.search(line)
            if match:
                src = match.group(1).strip()
                dst = match.group(2).strip()
                rep = (match.group(3) or "").strip().rstrip("*")
                msg = match.group(4).strip()

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
