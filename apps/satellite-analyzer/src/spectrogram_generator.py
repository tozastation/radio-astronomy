import logging
import os
import wave
import numpy as np
import matplotlib
matplotlib.use("Agg")  # ヘッドレス環境向けバックエンド
import matplotlib.pyplot as plt
from scipy import signal

logger = logging.getLogger(__name__)


class SpectrogramGenerator:
    """WAV 音声ファイルまたは生IQ (.raw / .u8) ファイルからドップラーS字カーブや複素ベースバンドスペクトルを可視化する PNG 画像ジェネレーター"""

    def __init__(self, nperseg: int = 1024, noverlap: int = 512):
        self.nperseg = nperseg
        self.noverlap = noverlap

    @staticmethod
    def _is_raw_iq_file(file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        return ext in (".raw", ".u8", ".cu8", ".iq")

    def compute_complex_iq_spectrogram(
        self,
        raw_path: str,
        sample_rate: int = 240000,
        max_time_bins: int = 240,
    ):
        """8-bit unsigned 複素IQ (cu8) ファイル全体から両側パワースペクトル密度 (-fs/2 .. +fs/2) の時系列行列を算出する"""
        file_size = os.path.getsize(raw_path)
        n_complex = file_size // 2
        n_fft = self.nperseg

        if n_complex < n_fft:
            raw_bytes = np.fromfile(raw_path, dtype=np.uint8)
            pad_len = n_fft * 2 - len(raw_bytes)
            if pad_len > 0:
                raw_bytes = np.pad(raw_bytes, (0, pad_len), constant_values=128)
            i_f = (raw_bytes[0::2].astype(np.float32) - 127.5) / 127.5
            q_f = (raw_bytes[1::2].astype(np.float32) - 127.5) / 127.5
            iq = (i_f + 1j * q_f) - np.mean(i_f + 1j * q_f)
            window = np.hanning(n_fft).astype(np.float32)
            spec = np.fft.fftshift(np.fft.fft(iq[:n_fft] * window))
            psd = (np.abs(spec) ** 2) / (sample_rate * np.sum(window ** 2))
            sxx_db = 10.0 * np.log10(np.maximum(psd[:, None], 1e-16))
            freqs_khz = np.fft.fftshift(np.fft.fftfreq(n_fft, d=1.0 / sample_rate)) / 1000.0
            times_sec = np.array([max(n_complex / sample_rate, 0.1)], dtype=np.float32)
            return freqs_khz, times_sec, sxx_db

        # メモリマップで大容量 .raw ファイル (150MB+) もゼロコピーで安全に参照
        mm = np.memmap(raw_path, dtype=np.uint8, mode="r", shape=(n_complex, 2))
        total_blocks = n_complex // n_fft
        n_time_bins = min(max_time_bins, total_blocks)
        blocks_per_bin = max(1, total_blocks // n_time_bins)
        # 各時間ビン内で最大 64 ブロックを平均して分散を抑えつつ高速計算
        avg_blocks = min(blocks_per_bin, 64)
        max_start_block = max(0, total_blocks - avg_blocks)

        window = np.hanning(n_fft).astype(np.float32)
        win_norm = float(sample_rate * np.sum(window ** 2))
        freqs_khz = np.fft.fftshift(np.fft.fftfreq(n_fft, d=1.0 / sample_rate)) / 1000.0
        times_sec = np.empty(n_time_bins, dtype=np.float32)
        sxx_db = np.empty((n_fft, n_time_bins), dtype=np.float32)

        for b in range(n_time_bins):
            start_block = (
                int(round(b * max_start_block / (n_time_bins - 1)))
                if n_time_bins > 1
                else 0
            )
            start_sample = start_block * n_fft
            end_sample = start_sample + avg_blocks * n_fft
            chunk = mm[start_sample:end_sample]
            i_f = (chunk[:, 0].astype(np.float32) - 127.5) / 127.5
            q_f = (chunk[:, 1].astype(np.float32) - 127.5) / 127.5
            iq = (i_f - np.mean(i_f)) + 1j * (q_f - np.mean(q_f))
            iq_blocks = iq.reshape(avg_blocks, n_fft) * window
            spec = np.fft.fftshift(np.fft.fft(iq_blocks, axis=1), axes=1)
            psd = np.mean(np.abs(spec) ** 2, axis=0) / win_norm
            # DC スパイク (中心 3 ビン) を隣接ビン平均で補間して視認性を改善
            mid = n_fft // 2
            psd[mid - 1 : mid + 2] = 0.5 * (psd[mid - 2] + psd[mid + 2])
            sxx_db[:, b] = 10.0 * np.log10(np.maximum(psd, 1e-16))
            times_sec[b] = (start_sample + 0.5 * avg_blocks * n_fft) / sample_rate

        return freqs_khz, times_sec, sxx_db

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

            # 非WAVバイナリ (生IQ .u8 / .raw や .cadu フレーム) の安全なフォールバック
            logger.info(f"SpectrogramGenerator: Processing non-WAV raw stream ({len(data)} bytes) with synthetic 48kHz audio.")
            framerate = 48000
            # 8-bit unsigned を 16-bit signed PCM へマッピング
            u8_arr = np.frombuffer(data[:48000 * 20], dtype=np.uint8)  # 最大20秒分
            s16_arr = ((u8_arr.astype(np.int32) - 128) * 256).astype(np.int16)
            return framerate, s16_arr.tobytes()

    def generate(
        self,
        wav_path: str,
        output_png_path: str,
        title: str = "Satellite Pass Spectrogram",
        sample_rate: int = 240000,
    ) -> str:
        """WAV ファイルまたは生IQ (.raw/.u8) ファイルからスペクトログラムを計算し、PNG 画像として保存する"""
        if self._is_raw_iq_file(wav_path) and os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
            freqs_khz, times, sxx_db = self.compute_complex_iq_spectrogram(
                wav_path, sample_rate=sample_rate
            )
            fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
            t_max = float(times[-1]) if len(times) > 0 else 1.0
            vmin = float(np.percentile(sxx_db, 5.0))
            vmax = float(np.percentile(sxx_db, 99.5))
            if vmax - vmin < 3.0:
                vmax = vmin + 6.0
            im = ax.imshow(
                sxx_db,
                aspect="auto",
                origin="lower",
                extent=[0.0, t_max, float(freqs_khz[0]), float(freqs_khz[-1])],
                cmap="viridis",
                vmin=vmin,
                vmax=vmax,
            )
            ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
            ax.set_xlabel("Time [seconds]", fontsize=11)
            ax.set_ylabel("Baseband Frequency Offset [kHz]", fontsize=11)
            ax.set_ylim(float(freqs_khz[0]), float(freqs_khz[-1]))
            cbar = fig.colorbar(im, ax=ax, orientation="vertical", pad=0.02)
            cbar.set_label("Power Spectral Density [dB/Hz]", fontsize=10)
            plt.tight_layout()
            plt.savefig(output_png_path, dpi=120)
            plt.close(fig)
            logger.info(f"SpectrogramGenerator: Successfully saved IQ spectrogram {output_png_path}")
            return output_png_path

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
