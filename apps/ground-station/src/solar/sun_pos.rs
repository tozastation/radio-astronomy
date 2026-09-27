use chrono::{DateTime, Utc};

/// 太陽の地平座標（方位角・高度）
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SunPosition {
    /// 方位角（北=0°, 東=90°, 南=180°, 西=270°）
    pub azimuth_deg: f64,
    /// 高度（地平線=0°, 天頂=90°, 地平線下はマイナス）
    pub elevation_deg: f64,
}

/// 観測地点（緯度・経度）と指定日時（UTC）における太陽の位置（方位角・高度）を計算します。
/// NOAA Solar Position アルゴリズム（Meeus天文学計算に基づく簡易高精度版）を採用。
///
/// # 引数
/// - `lat_deg`: 観測地点の緯度（北緯がプラス、南緯がマイナス: 例 35.68）
/// - `lon_deg`: 観測地点の経度（東経がプラス、西経がマイナス: 例 139.76）
/// - `time`: 観測日時（UTC）
pub fn calculate_sun_position(lat_deg: f64, lon_deg: f64, time: DateTime<Utc>) -> SunPosition {
    let lat_rad = lat_deg.to_radians();

    // ユリウス日 (Julian Day) の計算
    let unix_secs = time.timestamp() as f64 + (time.timestamp_subsec_nanos() as f64 / 1_000_000_000.0);
    let jd = 2440587.5 + (unix_secs / 86400.0);
    let t = (jd - 2451545.0) / 36525.0; // J2000.0 からのユリウス世紀数

    // 太陽の幾何平均黄経 (Geometric Mean Longitude) [度]
    let l0 = (280.46646 + 36000.76983 * t + 0.0003032 * t * t).rem_euclid(360.0);

    // 太陽の平均近点角 (Mean Anomaly) [度]
    let m = (357.52911 + 35999.05029 * t - 0.0001537 * t * t).rem_euclid(360.0);
    let m_rad = m.to_radians();

    // 太陽の中心差 (Equation of Center) [度]
    let c = (1.914602 - 0.004817 * t - 0.000014 * t * t) * m_rad.sin()
        + (0.019993 - 0.000101 * t) * (2.0 * m_rad).sin()
        + 0.000289 * (3.0 * m_rad).sin();

    // 太陽の真黄経 (True Longitude) [度]
    let true_long = l0 + c;
    let true_long_rad = true_long.to_radians();

    // 黄道傾斜角 (Mean Obliquity of Ecliptic) [度]
    let eps0 = 23.439291 - 0.0130042 * t - 0.00000016 * t * t;
    let eps_rad = eps0.to_radians();

    // 太陽の赤経 α (Right Ascension) と 赤緯 δ (Declination)
    let y = eps_rad.cos() * true_long_rad.sin();
    let x = true_long_rad.cos();
    let alpha = y.atan2(x); // [-π, π]
    let delta = (eps_rad.sin() * true_long_rad.sin()).asin();

    // グリニッジ平均恒星時 GMST [度]
    let gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)).rem_euclid(360.0);
    // 地方恒星時 LMST [rad]
    let lmst_rad = (gmst + lon_deg).rem_euclid(360.0).to_radians();

    // 時角 H (Hour Angle) [rad]
    let h = lmst_rad - alpha;

    // 地平座標系への変換 (Elevation / Azimuth)
    let sin_el = lat_rad.sin() * delta.sin() + lat_rad.cos() * delta.cos() * h.cos();
    let el = sin_el.clamp(-1.0, 1.0).asin();

    let cos_el = el.cos();
    let az = if cos_el.abs() < 1e-6 {
        // 天頂または天底に近い特異点
        0.0
    } else {
        let sin_az = -delta.cos() * h.sin() / cos_el;
        let cos_az = (delta.sin() - lat_rad.sin() * sin_el) / (lat_rad.cos() * cos_el);
        sin_az.atan2(cos_az).rem_euclid(2.0 * std::f64::consts::PI)
    };

    SunPosition {
        azimuth_deg: az.to_degrees(),
        elevation_deg: el.to_degrees(),
    }
}
