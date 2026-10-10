import logging
import wave
import numpy as np
import matplotlib
matplotlib.use("Agg")  # ヘッドレス環境向けバックエンド
import matplotlib.pyplot as plt
from scipy import signal

logger = logging.getLogger(__name__)


class SpectrogramGenerator:
    """WAV 音声ファイルからドップラーS字カーブやスペクトルを可視化する PNG 画像ジェネレーター"""

    def __init__(self, nperseg: int = 1024, noverlap: int = 512):
        self.nperseg = nperseg
        self.noverlap = noverlap

    def _load_audio_safely(self, wav_path: str):
        """破損したRIFFヘッダー（サイズ0など）を自己修復しつつWAVオーディオを読み込む"""
        try:
            with wave.open(wav_path, "rb") as wf:
                framerate = wf.getframerate()
                n_frames = wf.getnframes()
                audio_bytes = wf.readframes(n_frames)
                return framerate, audio_bytes
        except (wave.Error, EOFError):
            logger.warning(f"SpectrogramGenerator: Recovering corrupted WAV header for {wav_path}...")
            with open(wav_path, "rb") as f:
                data = bytearray(f.read())
            if len(data) >= 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
                file_len = len(data)
                data[4:8] = (file_len - 8).to_bytes(4, "little")
                data[40:44] = (file_len - 44).to_bytes(4, "little")
                import io
                with wave.open(io.BytesIO(data), "rb") as wf:
                    framerate = wf.getframerate()
                    n_frames = wf.getnframes()
                    audio_bytes = wf.readframes(n_frames)
                    return framerate, audio_bytes
            raise

    def generate(
        self,
        wav_path: str,
        output_png_path: str,
        title: str = "Satellite Pass Spectrogram",
    ) -> str:
        """WAV ファイルからスペクトログラムを計算し、PNG 画像として保存する"""
        framerate, audio_bytes = self._load_audio_safely(wav_path)

        # 16-bit PCM を浮動小数点配列へ変換
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        if len(audio) < self.nperseg:
            # サンプル数が極小の場合はゼロパディング
            audio = np.pad(audio, (0, self.nperseg - len(audio)))

        # 短時間フーリエ変換 (STFT) によるスペクトログラム計算
        frequencies, times, sxx = signal.spectrogram(
            audio,
            fs=framerate,
            window="hann",
            nperseg=self.nperseg,
            noverlap=self.noverlap,
            scaling="density",
        )

        # 対数パワースペクトル (dB)
        sxx_db = 10.0 * np.log10(np.maximum(sxx, 1e-12))

        # プロット作成（巨大データでもメモリ消費を抑え、高速描画可能な imshow を使用）
        fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
        t_max = times[-1] if len(times) > 0 else 1.0
        im = ax.imshow(
            sxx_db,
            aspect="auto",
            origin="lower",
            extent=[times[0] if len(times) > 0 else 0.0, t_max, 0, framerate / 2],
            cmap="viridis",
        )

        ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
        ax.set_xlabel("Time [seconds]", fontsize=11)
        ax.set_ylabel("Frequency [Hz]", fontsize=11)
        ax.set_ylim(0, framerate / 2)

        cbar = fig.colorbar(im, ax=ax, orientation="vertical", pad=0.02)
        cbar.set_label("Power Spectral Density [dB/Hz]", fontsize=10)

        plt.tight_layout()
        plt.savefig(output_png_path, dpi=120)
        plt.close(fig)

        logger.info(f"SpectrogramGenerator: Successfully saved {output_png_path}")
        return output_png_path
