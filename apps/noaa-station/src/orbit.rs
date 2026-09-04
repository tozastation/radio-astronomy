use crate::config::ObserverConfig;
use anyhow::{Context, Result};
use chrono::{DateTime, Duration, TimeZone, Utc};
use log::info;
use reqwest::Client;
use sgp4::{Constants, Elements};

/// 対象衛星の情報（TLE・受信周波数）
#[derive(Debug, Clone)]
pub struct SatelliteInfo {
    pub name: String,
    pub norad_id: u32,
    pub frequency_hz: u64,
    pub line1: String,
    pub line2: String,
}

/// 検出された衛星通過イベント
#[derive(Debug, Clone)]
pub struct SatellitePass {
    pub satellite_name: String,
    pub frequency_hz: u64,
    pub aos: DateTime<Utc>,
    pub los: DateTime<Utc>,
    pub max_elevation_deg: f64,
}

pub struct OrbitPredictor;

impl OrbitPredictor {
    /// 単一の衛星について、指定期間（duration_hours）内の通過パスを計算
    pub fn predict_passes_for_satellite(
        sat: &SatelliteInfo,
        observer: &ObserverConfig,
        start_time: DateTime<Utc>,
        duration_hours: u64,
        min_el_deg: f64,
    ) -> Result<Vec<SatellitePass>> {
        let elements = Elements::from_tle(
            Some(sat.name.clone()),
            sat.line1.as_bytes(),
            sat.line2.as_bytes(),
        )
        .map_err(|e| anyhow::anyhow!("TLEのパースに失敗しました: {:?}", e))?;

        let constants = Constants::from_elements(&elements)
            .map_err(|e| anyhow::anyhow!("SGP4 Constantsの初期化に失敗しました: {:?}", e))?;

        // 観測地点のECEF座標 (km) を事前計算
        let obs_ecef = geodetic_to_ecef(observer.latitude, observer.longitude, observer.altitude_m);

        let mut passes = Vec::new();
        let mut in_pass = false;
        let mut current_aos = start_time;
        let mut max_el = 0.0f64;

        // 30秒刻みでスキャン
        let step_seconds = 30i64;
        let total_steps = (duration_hours as i64 * 3600) / step_seconds;

        for i in 0..=total_steps {
            let t = start_time + Duration::seconds(i * step_seconds);
            let el = calculate_elevation(&elements, &constants, &obs_ecef, observer.latitude, observer.longitude, t);

            if el >= min_el_deg {
                if !in_pass {
                    in_pass = true;
                    current_aos = t;
                    max_el = el;
                } else if el > max_el {
                    max_el = el;
                }
            } else if in_pass {
                in_pass = false;
                passes.push(SatellitePass {
                    satellite_name: sat.name.clone(),
                    frequency_hz: sat.frequency_hz,
                    aos: current_aos,
                    los: t,
                    max_elevation_deg: max_el,
                });
                max_el = 0.0;
            }
        }

        // スキャン終了時にまだパスが継続していた場合
        if in_pass {
            passes.push(SatellitePass {
                satellite_name: sat.name.clone(),
                frequency_hz: sat.frequency_hz,
                aos: current_aos,
                los: start_time + Duration::hours(duration_hours as i64),
                max_elevation_deg: max_el,
            });
        }

        Ok(passes)
    }

    /// 複数衛星の通過パスを予測し、時系列順にソート＆重複調整して返す
    pub fn predict_all_passes(
        satellites: &[SatelliteInfo],
        observer: &ObserverConfig,
        start_time: DateTime<Utc>,
        duration_hours: u64,
        min_el_deg: f64,
    ) -> Result<Vec<SatellitePass>> {
        let mut all_passes = Vec::new();
        for sat in satellites {
            let passes = Self::predict_passes_for_satellite(
                sat,
                observer,
                start_time,
                duration_hours,
                min_el_deg,
            )?;
            all_passes.extend(passes);
        }

        // AOS順にソート
        all_passes.sort_by_key(|p| p.aos);

        // 重複するパスがある場合は最大仰角が高い方を優先
        let mut resolved = Vec::new();
        for pass in all_passes {
            if let Some(last) = resolved.last_mut() {
                let last_pass: &mut SatellitePass = last;
                if pass.aos < last_pass.los {
                    // 時間が重複している場合
                    if pass.max_elevation_deg > last_pass.max_elevation_deg {
                        // 新しいパスの方が仰角が高いので差し替え
                        *last_pass = pass;
                    }
                    continue;
                }
            }
            resolved.push(pass);
        }

        Ok(resolved)
    }
}

/// CelesTrak から対象の気象衛星（NOAA 15, 18, 19）の TLE を取得
pub async fn fetch_weather_tles(client: &Client) -> Result<Vec<SatelliteInfo>> {
    let targets = [
        ("NOAA 15", 25338, 137_620_000),
        ("NOAA 18", 28654, 137_912_500),
        ("NOAA 19", 33591, 137_100_000),
    ];

    let mut results = Vec::new();
    for (name, norad_id, freq) in targets {
        let url = format!(
            "https://celestrak.org/NORAD/elements/gp.php?CATNR={}&FORMAT=tle",
            norad_id
        );
        info!("TLE 取得中: {} (NORAD ID: {})", name, norad_id);

        let resp = client
            .get(&url)
            .send()
            .await
            .with_context(|| format!("TLE取得失敗: {}", name))?;

        let text = resp
            .text()
            .await
            .with_context(|| format!("レスポンステキスト取得失敗: {}", name))?;

        let lines: Vec<&str> = text
            .lines()
            .map(|s| s.trim())
            .filter(|s| !s.is_empty())
            .collect();

        if lines.len() >= 3 && lines[1].starts_with("1 ") && lines[2].starts_with("2 ") {
            results.push(SatelliteInfo {
                name: name.to_string(),
                norad_id,
                frequency_hz: freq,
                line1: lines[1].to_string(),
                line2: lines[2].to_string(),
            });
        }
    }

    Ok(results)
}

/// 観測地（緯度・経度・標高）の WGS84 ECEF 座標 (km) を算出
fn geodetic_to_ecef(lat_deg: f64, lon_deg: f64, alt_m: f64) -> [f64; 3] {
    let lat = lat_deg.to_radians();
    let lon = lon_deg.to_radians();
    let alt_km = alt_m / 1000.0;

    let a = 6378.137; // 地球赤道半径 (km)
    let f = 1.0 / 298.257223563; // 扁平率
    let e2 = f * (2.0 - f); // 第一離心率の2乗

    let n = a / (1.0 - e2 * lat.sin().powi(2)).sqrt();

    let x = (n + alt_km) * lat.cos() * lon.cos();
    let y = (n + alt_km) * lat.cos() * lon.sin();
    let z = (n * (1.0 - e2) + alt_km) * lat.sin();

    [x, y, z]
}

/// 指定時刻における衛星の仰角 (度) を計算
fn calculate_elevation(
    elements: &Elements,
    constants: &Constants,
    obs_ecef: &[f64; 3],
    lat_deg: f64,
    lon_deg: f64,
    t: DateTime<Utc>,
) -> f64 {
    // TLE エポックからの経過分 (minutes) を計算
    let epoch_dt = sgp4_epoch_to_datetime(elements.epoch());
    let diff = t.signed_duration_since(epoch_dt);
    let minutes_since_epoch = diff.num_milliseconds() as f64 / 60_000.0;

    let prediction = match constants.propagate(minutes_since_epoch) {
        Ok(p) => p,
        Err(_) => return -90.0,
    };

    // 衛星の ECI 座標 (km)
    let sat_eci = [
        prediction.position[0],
        prediction.position[1],
        prediction.position[2],
    ];

    // グリニッジ恒星時 (GMST) 角 [rad]
    let gmst = calculate_gmst(t);

    // ECI -> ECEF 回転
    let cos_g = gmst.cos();
    let sin_g = gmst.sin();
    let sat_ecef = [
        cos_g * sat_eci[0] + sin_g * sat_eci[1],
        -sin_g * sat_eci[0] + cos_g * sat_eci[1],
        sat_eci[2],
    ];

    // 観測点から衛星への相対ベクトル
    let rx = sat_ecef[0] - obs_ecef[0];
    let ry = sat_ecef[1] - obs_ecef[1];
    let rz = sat_ecef[2] - obs_ecef[2];

    // Topocentric 水平座標系 (East, North, Up)
    let lat = lat_deg.to_radians();
    let lon = lon_deg.to_radians();

    let sin_lat = lat.sin();
    let cos_lat = lat.cos();
    let sin_lon = lon.sin();
    let cos_lon = lon.cos();

    let _east = -sin_lon * rx + cos_lon * ry;
    let _north = -sin_lat * cos_lon * rx - sin_lat * sin_lon * ry + cos_lat * rz;
    let up = cos_lat * cos_lon * rx + cos_lat * sin_lon * ry + sin_lat * rz;

    let range = (rx.powi(2) + ry.powi(2) + rz.powi(2)).sqrt();
    if range < 1e-6 {
        return -90.0;
    }

    let sin_el = up / range;
    let el_rad = sin_el.clamp(-1.0, 1.0).asin();
    el_rad.to_degrees()
}

/// SGP4 の epoch (YYDDD.DDDDDD) を chrono::DateTime<Utc> に変換
fn sgp4_epoch_to_datetime(epoch: f64) -> DateTime<Utc> {
    let year_prefix = epoch as i32 / 1000;
    let full_year = if year_prefix < 57 {
        2000 + year_prefix
    } else {
        1900 + year_prefix
    };
    let day_of_year = epoch - (year_prefix * 1000) as f64;
    let whole_days = day_of_year.floor() as i64;
    let day_fraction = day_of_year - whole_days as f64;

    let jan1 = Utc.with_ymd_and_hms(full_year, 1, 1, 0, 0, 0).unwrap();
    let seconds = ((whole_days - 1) as f64 + day_fraction) * 86400.0;
    jan1 + Duration::milliseconds((seconds * 1000.0) as i64)
}

/// 指定 UTC 日時におけるグリニッジ平均恒星時 (GMST) を算出 (rad)
fn calculate_gmst(t: DateTime<Utc>) -> f64 {
    let ts = t.timestamp() as f64;
    let jd = (ts / 86400.0) + 2440587.5;
    let d = jd - 2451545.0; // J2000.0 からの日数

    let gmst_deg = 280.46061837 + 360.98564736629 * d;
    let gmst_deg_norm = gmst_deg.rem_euclid(360.0);
    gmst_deg_norm.to_radians()
}
