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
        生 IQ ファイル (240kSPS uint8 cu8) からドップラー補正・FM復調を行い、48kHz WAV を生成する
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

        # 1. 生 IQ 読み込み (uint8: [I0, Q0, I1, Q1, ...])
        raw_u8 = np.fromfile(raw_path, dtype=np.uint8)
        num_complex_samples = len(raw_u8) // 2
        if num_complex_samples < sample_rate * 0.1:
            return ""

        # 2. 複素解析信号化 x[n] = (I - 127.5)/128 + j (Q - 127.5)/128
        i_comp = (raw_u8[0::2].astype(np.float32) - 127.5) / 128.0
        q_comp = (raw_u8[1::2].astype(np.float32) - 127.5) / 128.0
        x = i_comp + 1j * q_comp
        del raw_u8, i_comp, q_comp

        duration_sec = num_complex_samples / float(sample_rate)

        # 3. ドップラー周波数偏移列の算出
        doppler_shifts = None
        if doppler_override_hz is not None:
            if len(doppler_override_hz) == num_complex_samples:
                doppler_shifts = doppler_override_hz
            else:
                x_interp = np.linspace(0, duration_sec, len(doppler_override_hz))
                x_samples = np.linspace(0, duration_sec, num_complex_samples)
                doppler_shifts = np.interp(x_samples, x_interp, doppler_override_hz)
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
                x_interp = np.linspace(0, duration_sec, len(shifts_1s))
                x_samples = np.linspace(0, duration_sec, num_complex_samples)
                doppler_shifts = np.interp(x_samples, x_interp, shifts_1s)
            except Exception as e:
                logger.warning(f"Failed to compute Doppler shift from TLE: {e}. Falling back to zero-shift.")
                doppler_shifts = None

        # 4. 複素ミキシングによるドップラー相殺 y[n] = x[n] * exp(-j θ[n])
        if doppler_shifts is not None:
            # 位相角 θ[n] = 2π * cumsum(Δf / f_s)
            phase_rad = 2.0 * np.pi * np.cumsum(doppler_shifts) / float(sample_rate)
            # exp(-j * phase) = cos(phase) - j * sin(phase)
            rotator = np.cos(phase_rad).astype(np.float32) - 1j * np.sin(phase_rad).astype(np.float32)
            y = x * rotator
            del x, rotator, phase_rad
        else:
            y = x

        # 5. 位相差分 FM 復調 (Polar Discriminator)
        # P[n] = y[n] * conj(y[n-1])
        # Δφ[n] = arg(P[n]) = atan2(Im(P[n]), Re(P[n]))
        p = y[1:] * np.conj(y[:-1])
        demod_audio = np.angle(p).astype(np.float32)
        del y, p

        # 6. FIR フィルタリング & 5倍デシメーション (240kSPS -> 48kSPS)
        # AFSK (Bell 202: 1200Hz/2200Hz) の不要高周波雑音を除去するためカットオフ 3.5kHz の LPF を適用
        b, a = signal.butter(4, 3500.0 / (sample_rate / 2.0), btype="low")
        filtered_audio = signal.filtfilt(b, a, demod_audio)
        del demod_audio

        # resample_poly で 1/5 にダウンサンプリング (240kHz / 5 = 48kHz)
        audio_48k = signal.resample_poly(filtered_audio, up=1, down=5)
        del filtered_audio

        # 7. 16-bit PCM WAV スケーリング & 出力
        max_val = np.max(np.abs(audio_48k))
        if max_val > 1e-6:
            # ピーク振幅を int16 の約 75% (24000) に正規化
            scale = 24000.0 / max_val
            pcm_data = np.clip(audio_48k * scale, -32768, 32767).astype(np.int16)
        else:
            pcm_data = np.zeros(len(audio_48k), dtype=np.int16)

        with wave.open(output_wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(48000)
            wf.writeframes(pcm_data.tobytes())

        logger.info(f"Successfully demodulated raw IQ to 48kHz WAV: {output_wav_path}")
        return output_wav_path
