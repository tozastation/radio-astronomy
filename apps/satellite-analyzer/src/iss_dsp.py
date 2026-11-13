import os
import math
import wave
import logging
from datetime import datetime, timezone, timedelta
from typing import Tuple, Optional, Union
import numpy as np
from scipy import signal
from sgp4.api import Satrec, jday

logger = logging.getLogger(__name__)

# 光速 [m/s]
SPEED_OF_LIGHT_M_S = 299792458.0

# WGS84 楕円体定数
WGS84_A_KM = 6378.137
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = 2 * WGS84_F - WGS84_F ** 2
EARTH_ROTATION_RATE_RAD_S = 7.2921151467e-5  # 地球自転角速度 [rad/s]


class ISSDspDemodulator:
    """
    RTL-SDR 生 IQ (240kSPS uint8 cu8) から、SGP4 軌道力学に基づく瞬時ドップラー相殺および
    位相差分 FM 復調 (Polar Discriminator) を行い、48kHz 16-bit モノラル WAV を生成する復調器
    """

    def __init__(self, lat: float = 35.6895, lon: float = 139.6917, alt_m: float = 30.0):
        self.lat_deg = lat
        self.lon_deg = lon
        self.alt_m = alt_m

        # 観測地の WGS84 ECEF 座標 [km] を事前計算
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        alt_km = alt_m / 1000.0

        n = WGS84_A_KM / math.sqrt(1.0 - WGS84_E2 * (math.sin(lat_rad) ** 2))
        self.obs_x_km = (n + alt_km) * math.cos(lat_rad) * math.cos(lon_rad)
        self.obs_y_km = (n + alt_km) * math.cos(lat_rad) * math.sin(lon_rad)
        self.obs_z_km = (n * (1.0 - WGS84_E2) + alt_km) * math.sin(lat_rad)

    def _teme_to_ecef(self, r_teme: np.ndarray, v_teme: np.ndarray, gmst_rad: float) -> Tuple[np.ndarray, np.ndarray]:
        """TEME 座標系から ECEF 座標系への変換 (地球自転効果を含む)"""
        cos_g = math.cos(gmst_rad)
        sin_g = math.sin(gmst_rad)

        # 位置ベクトル回転
        r_x = r_teme[0] * cos_g + r_teme[1] * sin_g
        r_y = -r_teme[0] * sin_g + r_teme[1] * cos_g
        r_z = r_teme[2]

        # 速度ベクトル回転 (地球自転に伴うコリオリ項 ω × r)
        v_x = v_teme[0] * cos_g + v_teme[1] * sin_g + EARTH_ROTATION_RATE_RAD_S * r_y
        v_y = -v_teme[0] * sin_g + v_teme[1] * cos_g - EARTH_ROTATION_RATE_RAD_S * r_x
        v_z = v_teme[2]

        return np.array([r_x, r_y, r_z]), np.array([v_x, v_y, v_z])

    def compute_doppler_shifts(
        self,
        tle_line1: str,
        tle_line2: str,
        start_time: datetime,
        duration_sec: float,
        center_freq_hz: float = 145825000.0,
        step_sec: float = 1.0,
    ) -> np.ndarray:
        """
        SGP4 軌道伝搬モデルを用いて、start_time から duration_sec 間における
        地上局から見た衛星の瞬時ドップラー周波数偏移 Δf(t) [Hz] を算出する
        Δf(t) = -f₀ * (v_r / c)
        """
        sat = Satrec.twoline2rv(tle_line1, tle_line2)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        num_steps = int(math.ceil(duration_sec / step_sec)) + 1
        doppler_shifts = []

        obs_r = np.array([self.obs_x_km, self.obs_y_km, self.obs_z_km])

        for step in range(num_steps):
            cur_dt = start_time + timedelta(seconds=step * step_sec)
            jd, fr = jday(
                cur_dt.year,
                cur_dt.month,
                cur_dt.day,
                cur_dt.hour,
                cur_dt.minute,
                cur_dt.second + cur_dt.microsecond * 1e-6,
            )

            e, r_teme, v_teme = sat.sgp4(jd, fr)
            if e != 0:
                doppler_shifts.append(0.0)
                continue

            # グリニッジ視恒星時 (GMST) の近似計算
            t_ut1 = (jd - 2451545.0) + fr
            theta = 280.46061837 + 360.98564736629 * t_ut1
            gmst_rad = math.radians(theta % 360.0)

            r_sat, v_sat = self._teme_to_ecef(np.array(r_teme), np.array(v_teme), gmst_rad)

            # 相対位置ベクトル (衛星 - 観測点)
            rho = r_sat - obs_r
            distance_km = np.linalg.norm(rho)
            if distance_km < 1e-3:
                doppler_shifts.append(0.0)
                continue

            # 視線単位ベクトル (観測点から衛星へ向かう方向)
            u_rho = rho / distance_km

            # 相対視線速度 v_r [m/s] (接近中: 負 / 離脱中: 正)
            v_r_km_s = np.dot(v_sat, u_rho)
            v_r_m_s = v_r_km_s * 1000.0

            # ドップラー周波数偏移 Δf = -f₀ * (v_r / c)
            delta_f = -center_freq_hz * (v_r_m_s / SPEED_OF_LIGHT_M_S)
            doppler_shifts.append(delta_f)

        return np.array(doppler_shifts, dtype=np.float64)

    def demodulate_raw_iq(
        self,
        raw_path: str,
        pass_start_utc: Optional[datetime] = None,
        tle_lines: Optional[Tuple[str, str]] = None,
        center_freq_hz: float = 145825000.0,
        sample_rate: int = 240000,
        output_wav_path: Optional[str] = None,
        doppler_override_hz: Optional[np.ndarray] = None,
    ) -> str:
        """
        生 IQ ファイル (240kSPS uint8 cu8) からドップラー補正・FM復調を行い、48kHz WAV を生成する。
        大容量ファイルでも OOM を起こさないよう 10 秒単位のチャンクストリーミングで処理する。
        """
        if not os.path.exists(raw_path):
            logger.warning(f"Raw IQ file not found: {raw_path}")
            return ""

        file_size = os.path.getsize(raw_path)
        # 100KB 未満は破損または空ファイルとみなす
        if file_size < 100 * 1024:
            logger.warning(f"Raw IQ file too small ({file_size} bytes): {raw_path}")
            return ""

        if output_wav_path is None:
            output_wav_path = os.path.splitext(raw_path)[0] + "_48k.wav"

        num_complex_samples = file_size // 2
        if num_complex_samples < sample_rate * 0.1:
            return ""

        duration_sec = num_complex_samples / float(sample_rate)

        # 1. ドップラー周波数偏移列 (1秒刻み等) の事前計算 (数百要素のみで軽量)
        shifts_1s = None
        override_arr = None
        if doppler_override_hz is not None:
            override_arr = np.asarray(doppler_override_hz, dtype=np.float64)
        elif tle_lines is not None and pass_start_utc is not None:
            try:
                shifts_1s = self.compute_doppler_shifts(
                    tle_line1=tle_lines[0],
                    tle_line2=tle_lines[1],
                    start_time=pass_start_utc,
                    duration_sec=duration_sec,
                    center_freq_hz=center_freq_hz,
                    step_sec=1.0,
                )
            except Exception as e:
                logger.warning(f"Failed to compute Doppler shift from TLE: {e}. Falling back to zero-shift.")
                shifts_1s = None

        # 2. チャンクストリーミング処理の設定
        decim = 5
        fs_if = sample_rate // decim  # 48000 Hz
        chunk_complex = (sample_rate * 10 // decim) * decim  # 10秒分 (240kSPSなら 2,400,000 サンプル)
        pad_in = 500  # resample_poly 境界過渡応答除去用オーバーラップ (500 % 5 == 0)
        pad_out = pad_in // decim

        sos = signal.butter(4, 3500.0 / (fs_if / 2.0), btype="low", output="sos")
        zi = np.zeros((sos.shape[0], 2), dtype=np.float64)

        phase_acc = 0.0
        prev_tail_x = None
        prev_x_if_last = None
        max_val = 0.0
        total_audio_samples = 0

        tmp_f32_path = output_wav_path + ".f32.tmp"
        try:
            with open(raw_path, "rb") as raw_f, open(tmp_f32_path, "wb") as tmp_f:
                sample_offset = 0
                while sample_offset < num_complex_samples:
                    to_read_complex = min(chunk_complex, num_complex_samples - sample_offset)
                    buf = raw_f.read(to_read_complex * 2)
                    if not buf or len(buf) < 2:
                        break
                    raw_u8 = np.frombuffer(buf, dtype=np.uint8)
                    actual_complex = len(raw_u8) // 2
                    if actual_complex == 0:
                        break

                    # 複素解析信号化 (complex64)
                    x = np.empty(actual_complex, dtype=np.complex64)
                    x.real = (raw_u8[0 : actual_complex * 2 : 2].astype(np.float32) - 127.5) / 128.0
                    x.imag = (raw_u8[1 : actual_complex * 2 : 2].astype(np.float32) - 127.5) / 128.0
                    del raw_u8

                    # ドップラー周波数偏移列 (このチャンク分のみ補間)
                    chunk_shifts = None
                    if override_arr is not None:
                        if len(override_arr) == num_complex_samples:
                            chunk_shifts = override_arr[sample_offset : sample_offset + actual_complex]
                        else:
                            x_interp = np.linspace(0.0, duration_sec, len(override_arr))
                            t0 = sample_offset / float(sample_rate)
                            t1 = (sample_offset + actual_complex) / float(sample_rate)
                            x_samples = np.linspace(t0, t1, actual_complex, endpoint=False)
                            chunk_shifts = np.interp(x_samples, x_interp, override_arr)
                    elif shifts_1s is not None:
                        x_interp = np.linspace(0.0, duration_sec, len(shifts_1s))
                        t0 = sample_offset / float(sample_rate)
                        t1 = (sample_offset + actual_complex) / float(sample_rate)
                        x_samples = np.linspace(t0, t1, actual_complex, endpoint=False)
                        chunk_shifts = np.interp(x_samples, x_interp, shifts_1s)

                    # 複素ミキシングによるドップラー相殺 (位相連続性を保持)
                    if chunk_shifts is not None:
                        phase_rad = phase_acc + (2.0 * np.pi * np.cumsum(chunk_shifts, dtype=np.float64) / float(sample_rate))
                        phase_acc = float(phase_rad[-1] % (2.0 * np.pi))
                        phase_f32 = phase_rad.astype(np.float32)
                        del phase_rad, chunk_shifts
                        rotator = np.empty(actual_complex, dtype=np.complex64)
                        rotator.real = np.cos(phase_f32)
                        rotator.imag = -np.sin(phase_f32)
                        del phase_f32
                        x *= rotator
                        del rotator

                    # 検波前チャネル帯域制限フィルタ & デシメーション (240kSPS -> 48kSPS)
                    if prev_tail_x is not None:
                        x_padded = np.concatenate((prev_tail_x, x))
                        x_real = signal.resample_poly(x_padded.real, up=1, down=decim)
                        x_imag = signal.resample_poly(x_padded.imag, up=1, down=decim)
                        x_if = (x_real[pad_out:] + 1j * x_imag[pad_out:]).astype(np.complex64)
                        del x_padded, x_real, x_imag
                    else:
                        x_real = signal.resample_poly(x.real, up=1, down=decim)
                        x_imag = signal.resample_poly(x.imag, up=1, down=decim)
                        x_if = (x_real + 1j * x_imag).astype(np.complex64)
                        del x_real, x_imag

                    if actual_complex >= pad_in:
                        prev_tail_x = x[-pad_in:].copy()
                    else:
                        prev_tail_x = None
                    del x

                    if len(x_if) == 0:
                        sample_offset += actual_complex
                        continue

                    # 位相差分 FM 復調 (Polar Discriminator at 48kSPS)
                    if prev_x_if_last is not None:
                        x_for_disc = np.concatenate((np.array([prev_x_if_last], dtype=np.complex64), x_if))
                        p = x_for_disc[1:] * np.conj(x_for_disc[:-1])
                        del x_for_disc
                    else:
                        if len(x_if) < 2:
                            prev_x_if_last = x_if[-1]
                            sample_offset += actual_complex
                            continue
                        p = x_if[1:] * np.conj(x_if[:-1])

                    prev_x_if_last = x_if[-1]
                    del x_if

                    demod_audio = np.angle(p).astype(np.float64)
                    del p

                    # 音声帯域フィルタリング (AFSK 3.5kHz LPF, 状態 zi をチャンク間で引き継ぎ)
                    audio_chunk, zi = signal.sosfilt(sos, demod_audio, zi=zi)
                    del demod_audio
                    audio_f32 = audio_chunk.astype(np.float32)
                    del audio_chunk

                    chunk_max = float(np.max(np.abs(audio_f32)))
                    if chunk_max > max_val:
                        max_val = chunk_max
                    total_audio_samples += len(audio_f32)
                    audio_f32.tofile(tmp_f)
                    del audio_f32

                    sample_offset += actual_complex

            # 3. 16-bit PCM WAV スケーリング & 出力 (ストリーミング書き出し)
            scale = (24000.0 / max_val) if max_val > 1e-6 else 0.0
            read_block_samples = fs_if * 10  # 480,000 samples (1.92 MB)
            with open(tmp_f32_path, "rb") as tmp_f, wave.open(output_wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(fs_if)
                while True:
                    chunk = np.fromfile(tmp_f, dtype=np.float32, count=read_block_samples)
                    if len(chunk) == 0:
                        break
                    if scale > 0.0:
                        pcm_data = np.clip(chunk * scale, -32768, 32767).astype(np.int16)
                    else:
                        pcm_data = np.zeros(len(chunk), dtype=np.int16)
                    wf.writeframes(pcm_data.tobytes())
        finally:
            if os.path.exists(tmp_f32_path):
                os.remove(tmp_f32_path)

        logger.info(f"Successfully demodulated raw IQ to 48kHz WAV: {output_wav_path}")
        return output_wav_path

    def estimate_tca_drift(
        self,
        file_path: str,
        pass_start_utc: Optional[datetime] = None,
        tle_lines: Optional[Tuple[str, str]] = None,
        center_freq_hz: float = 145825000.0,
        sample_rate: int = 240000,
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        実録音データ (生 IQ または WAV) の時間ごとの受信電力エンベロープを解析し、
        実測 TCA と SGP4 予測 TCA のタイムドリフト Δt [秒] (t_actual - t_sgp4)
        および推定ピーク SNR [dB] を算出する。
        信号が有意でない場合 (SNR < 2.0 dB 等) は (None, None) を返す。
        """
        if not os.path.exists(file_path) or tle_lines is None or pass_start_utc is None:
            return None, None

        file_size = os.path.getsize(file_path)
        if file_size < 100 * 1024:
            return None, None

        ext = os.path.splitext(file_path)[1].lower()
        power_env = []
        step_dt = 1.0  # 1秒ごとの平均電力

        if ext in (".raw", ".u8", ".cu8"):
            # 生 IQ (240kSPS uint8: 1秒あたり 480,000 bytes)
            bytes_per_sec = sample_rate * 2
            with open(file_path, "rb") as f:
                while True:
                    buf = f.read(bytes_per_sec)
                    if len(buf) < bytes_per_sec // 2:
                        break
                    u8 = np.frombuffer(buf, dtype=np.uint8)
                    i = (u8[0::2].astype(np.float32) - 127.5) / 128.0
                    q = (u8[1::2].astype(np.float32) - 127.5) / 128.0
                    p = np.mean(i ** 2 + q ** 2)
                    power_env.append(float(p))
        elif ext == ".wav":
            try:
                with wave.open(file_path, "rb") as wf:
                    n_channels = wf.getnchannels()
                    sampwidth = wf.getsampwidth()
                    frate = wf.getframerate()
                    frames_per_sec = frate
                    while True:
                        buf = wf.readframes(frames_per_sec)
                        if not buf:
                            break
                        if sampwidth == 2:
                            audio = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
                        else:
                            audio = np.frombuffer(buf, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
                        if n_channels > 1:
                            audio = audio[0::n_channels]
                        p = np.mean(audio ** 2)
                        power_env.append(float(p))
            except Exception as e:
                logger.warning(f"Error reading WAV for TCA drift estimation: {e}")
                return None, None
        else:
            return None, None

        if len(power_env) < 10:
            return None, None

        power_arr = np.array(power_env, dtype=np.float64)
        # 移動平均スムージング (窓幅 7秒) で瞬間的なノイズスパイクを除去
        window_len = min(7, len(power_arr))
        if window_len >= 3:
            kernel = np.ones(window_len) / window_len
            smooth_power = np.convolve(power_arr, kernel, mode="same")
        else:
            smooth_power = power_arr

        # ノイズフロア (中央値) とピーク値から推定 SNR [dB] を計算
        noise_floor = float(np.median(smooth_power))
        peak_power = float(np.max(smooth_power))
        if noise_floor <= 1e-12:
            return None, None

        snr_db = 10.0 * math.log10(peak_power / noise_floor)
        if snr_db < 2.0:
            # 信号有意性なし (アンビエントノイズのみ)
            return None, round(snr_db, 1)

        actual_peak_sec = float(np.argmax(smooth_power))

        # SGP4 理論ドップラー偏移列のゼロクロス点 (TCA) を特定
        duration_sec = float(len(power_env))
        d_shifts = self.compute_doppler_shifts(
            tle_line1=tle_lines[0],
            tle_line2=tle_lines[1],
            start_time=pass_start_utc,
            duration_sec=duration_sec,
            center_freq_hz=center_freq_hz,
            step_sec=1.0,
        )
        if len(d_shifts) < 2:
            return None, round(snr_db, 1)

        # ドップラー絶対値が最小となる秒数 (TCA: ゼロクロス)
        sgp4_tca_sec = float(np.argmin(np.abs(d_shifts)))

        drift_sec = actual_peak_sec - sgp4_tca_sec
        # 物理的妥当性フィルタ: 軌道伝搬誤差が LEO で ±60秒を超えることは通常ない
        if abs(drift_sec) > 60.0:
            return None, round(snr_db, 1)

        return round(drift_sec, 1), round(snr_db, 1)
