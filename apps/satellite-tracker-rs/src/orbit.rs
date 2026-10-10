use anyhow::{anyhow, Result};
use chrono::{DateTime, Duration, Utc};
use sgp4::{Constants, Elements};

pub const SPEED_OF_LIGHT_M_S: f64 = 299_792_458.0;

/// 衛星の瞬間位置・観測パラメータ
#[derive(Debug, Clone, PartialEq)]
pub struct SatellitePosition {
    pub elevation_deg: f64,
    pub azimuth_deg: f64,
    pub range_km: f64,
    pub radial_velocity_km_s: f64,
    pub doppler_shift_hz: f64,
}

/// 衛星通過イベント（パス情報）
#[derive(Debug, Clone, PartialEq)]
pub struct PassInfo {
    pub satellite_name: String,
    pub aos_time: DateTime<Utc>,
    pub tca_time: DateTime<Utc>,
    pub los_time: DateTime<Utc>,
    pub max_elevation_deg: f64,
    pub frequency_hz: f64,
}

/// SGP4 軌道予測・ドップラー偏移・ベランダ視界判定エンジン
#[derive(Debug, Clone)]
pub struct OrbitPredictor {
    pub observer_lat: f64,
    pub observer_lon: f64,
    pub observer_alt_m: f64,
    pub balcony_facing: String,
    pub min_elevation_deg: f64,
    obs_ecef: [f64; 3],
}

impl OrbitPredictor {
    /// 新規 OrbitPredictor を生成
    pub fn new(
        observer_lat: f64,
        observer_lon: f64,
        observer_alt_m: f64,
        balcony_facing: &str,
        min_elevation_deg: f64,
    ) -> Self {
        let obs_ecef = geodetic_to_ecef(observer_lat, observer_lon, observer_alt_m);
        Self {
            observer_lat,
            observer_lon,
            observer_alt_m,
            balcony_facing: balcony_facing.to_uppercase(),
            min_elevation_deg,
            obs_ecef,
        }
    }

    /// 指定した仰角・方位角がベランダ視界内にあるか判定
    pub fn is_in_view(&self, elevation_deg: f64, azimuth_deg: f64) -> bool {
        if elevation_deg < self.min_elevation_deg {
            return false;
        }

        if self.balcony_facing == "NORTH" {
            // 北向きベランダ: 方位角 270°〜360° または 0°〜90° (西〜北〜東)
            let az = azimuth_deg.rem_euclid(360.0);
            (270.0..=360.0).contains(&az) || (0.0..=90.0).contains(&az)
        } else {
            // 全天開放 (ALL 等)
            true
        }
    }

    /// 指定時刻における衛星の位置、視線速度、ドップラー偏移を算出
    pub fn calculate_position(
        &self,
        sat_name: &str,
        tle_line1: &str,
        tle_line2: &str,
        freq_hz: f64,
        timestamp: DateTime<Utc>,
    ) -> Result<SatellitePosition> {
        let elements = Elements::from_tle(
            Some(sat_name.to_string()),
            tle_line1.as_bytes(),
            tle_line2.as_bytes(),
        )
        .map_err(|e| anyhow!("TLEのパースに失敗しました: {:?}", e))?;

        let constants = Constants::from_elements(&elements)
            .map_err(|e| anyhow!("SGP4 Constantsの初期化に失敗しました: {:?}", e))?;

        // 基準時刻での位置計算
        let (el, az, range_km) = calculate_topo_pos_and_range(
            &elements,
            &constants,
            &self.obs_ecef,
            self.observer_lat,
            self.observer_lon,
            timestamp,
        )?;

        // 微小時間差分 (dt = 0.5s) による数値微分で視線速度 (Radial velocity) を算出
        let dt_sec = 0.5f64;
        let t_plus = timestamp + Duration::milliseconds((dt_sec * 1000.0) as i64);
        let t_minus = timestamp - Duration::milliseconds((dt_sec * 1000.0) as i64);

        let (_, _, range_plus) = calculate_topo_pos_and_range(
            &elements,
            &constants,
            &self.obs_ecef,
            self.observer_lat,
            self.observer_lon,
            t_plus,
        )?;
        let (_, _, range_minus) = calculate_topo_pos_and_range(
            &elements,
            &constants,
            &self.obs_ecef,
            self.observer_lat,
            self.observer_lon,
            t_minus,
        )?;

        let radial_velocity_km_s = (range_plus - range_minus) / (2.0 * dt_sec);

        // ドップラー偏移: Delta_f = - f0 * (vr / c)
        let vr_m_s = radial_velocity_km_s * 1000.0;
        let doppler_shift_hz = -freq_hz * (vr_m_s / SPEED_OF_LIGHT_M_S);

        Ok(SatellitePosition {
            elevation_deg: el,
            azimuth_deg: az,
            range_km,
            radial_velocity_km_s,
            doppler_shift_hz,
        })
    }

    /// ベランダ視界内に現れる次期パス（AOS, TCA, LOS）を探索
    pub fn get_next_pass(
        &self,
        sat_name: &str,
        tle_line1: &str,
        tle_line2: &str,
        freq_hz: f64,
        start_time: DateTime<Utc>,
        search_hours: f64,
        step_seconds: i64,
    ) -> Result<Option<PassInfo>> {
        let elements = Elements::from_tle(
            Some(sat_name.to_string()),
            tle_line1.as_bytes(),
            tle_line2.as_bytes(),
        )
        .map_err(|e| anyhow!("TLEパース失敗: {:?}", e))?;

        let constants = Constants::from_elements(&elements)
            .map_err(|e| anyhow!("SGP4初期化失敗: {:?}", e))?;

        let total_steps = ((search_hours * 3600.0) / step_seconds as f64) as i64;
        let mut in_pass = false;
        let mut aos_time = None;
        let mut tca_time = None;
        let mut max_el = -90.0f64;

        for i in 0..=total_steps {
            let current_time = start_time + Duration::seconds(i * step_seconds);
            let topo = calculate_topo_pos_and_range(
                &elements,
                &constants,
                &self.obs_ecef,
                self.observer_lat,
                self.observer_lon,
                current_time,
            );

            let (el, az) = match topo {
                Ok((el, az, _)) => (el, az),
                Err(_) => (-90.0, 0.0),
            };

            let is_visible = self.is_in_view(el, az);

            if !in_pass && is_visible {
                // パス開始 (AOS)
                in_pass = true;
                aos_time = Some(current_time);
                max_el = el;
                tca_time = Some(current_time);
            } else if in_pass && is_visible {
                // パス通過中 (TCA 更新)
                if el > max_el {
                    max_el = el;
                    tca_time = Some(current_time);
                }
            } else if in_pass && !is_visible {
                // パス終了 (LOS)
                let los_time = current_time;
                return Ok(Some(PassInfo {
                    satellite_name: sat_name.to_string(),
                    aos_time: aos_time.expect("AOS is set"),
                    tca_time: tca_time.expect("TCA is set"),
                    los_time,
                    max_elevation_deg: max_el,
                    frequency_hz: freq_hz,
                }));
            }
        }

        Ok(None)
    }
}

/// 観測地（緯度・経度・標高）の WGS84 楕円体における ECEF 直交座標 (km) を算出
fn geodetic_to_ecef(lat_deg: f64, lon_deg: f64, alt_m: f64) -> [f64; 3] {
    let lat = lat_deg.to_radians();
    let lon = lon_deg.to_radians();
    let alt_km = alt_m / 1000.0;

    let a = 6378.137; // 地球赤道半径 (km)
    let f = 1.0 / 298.257223563; // 地球の扁平率
    let e2 = f * (2.0 - f); // 第一離心率の2乗

    let n = a / (1.0 - e2 * lat.sin().powi(2)).sqrt();

    let x = (n + alt_km) * lat.cos() * lon.cos();
    let y = (n + alt_km) * lat.cos() * lon.sin();
    let z = (n * (1.0 - e2) + alt_km) * lat.sin();

    [x, y, z]
}

/// 指定時刻における衛星の仰角 (度)、方位角 (度)、直線距離 (km) を算出
fn calculate_topo_pos_and_range(
    elements: &Elements,
    constants: &Constants,
    obs_ecef: &[f64; 3],
    lat_deg: f64,
    lon_deg: f64,
    t: DateTime<Utc>,
) -> Result<(f64, f64, f64)> {
    let epoch_dt = DateTime::<Utc>::from_naive_utc_and_offset(elements.datetime, Utc);
    let diff = t.signed_duration_since(epoch_dt);
    let minutes_since_epoch = diff.num_milliseconds() as f64 / 60_000.0;

    let prediction = constants
        .propagate(minutes_since_epoch)
        .map_err(|e| anyhow!("SGP4 propagate failed: {:?}", e))?;

    let sat_eci = [
        prediction.position[0],
        prediction.position[1],
        prediction.position[2],
    ];

    let gmst = calculate_gmst(t);
    let cos_g = gmst.cos();
    let sin_g = gmst.sin();
    let sat_ecef = [
        cos_g * sat_eci[0] + sin_g * sat_eci[1],
        -sin_g * sat_eci[0] + cos_g * sat_eci[1],
        sat_eci[2],
    ];

    let rx = sat_ecef[0] - obs_ecef[0];
    let ry = sat_ecef[1] - obs_ecef[1];
    let rz = sat_ecef[2] - obs_ecef[2];

    let lat = lat_deg.to_radians();
    let lon = lon_deg.to_radians();

    let sin_lat = lat.sin();
    let cos_lat = lat.cos();
    let sin_lon = lon.sin();
    let cos_lon = lon.cos();

    let east = -sin_lon * rx + cos_lon * ry;
    let north = -sin_lat * cos_lon * rx - sin_lat * sin_lon * ry + cos_lat * rz;
    let up = cos_lat * cos_lon * rx + cos_lat * sin_lon * ry + sin_lat * rz;

    let range = (rx.powi(2) + ry.powi(2) + rz.powi(2)).sqrt();
    if range < 1e-6 {
        return Ok((-90.0, 0.0, 0.0));
    }

    let sin_el = up / range;
    let el_rad = sin_el.clamp(-1.0, 1.0).asin();
    let el_deg = el_rad.to_degrees();

    let az_rad = east.atan2(north);
    let az_deg = az_rad.to_degrees().rem_euclid(360.0);

    Ok((el_deg, az_deg, range))
}

/// グリニッジ平均恒星時 (GMST) 角 [rad] (IAU 1982 公式)
fn calculate_gmst(t: DateTime<Utc>) -> f64 {
    let ts = t.timestamp() as f64;
    let jd = (ts / 86400.0) + 2440587.5;
    let d = jd - 2451545.0;

    let gmst_deg = 280.46061837 + 360.98564736629 * d;
    let gmst_deg_norm = gmst_deg.rem_euclid(360.0);
    gmst_deg_norm.to_radians()
}
