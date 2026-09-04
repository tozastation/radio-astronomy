use crate::config::{ObserverConfig, SatellitesConfig};
use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use log::info;
use reqwest::Client;
use sgp4::{Constants, Elements};

// =============================================================================
// 🛰️ 軌道計算 & パス予測モジュール (Orbit / SGP4)
// -----------------------------------------------------------------------------
// 【天文学 & DSP の概念と SRE 的対比】
// - TLE (Two-Line Element): 衛星の軌道パラメータを2行の文字列にエンコードした規格。
//   いわば「有効期限(TTL)付きの軌道状態スナップショット」です。大気抵抗等で徐々にズレるため、
//   本システムでは24時間ごとに CelesTrak から最新スナップショットをプルします。
// - ECI 座標系 (地心慣性系): 地球の中心を原点とし、宇宙空間に固定された座標系。
//   地球の自転の影響を受けない「絶対座標系」です。SGP4 はこの座標で衛星位置を出力します。
// - ECEF 座標系 (地心直交系): 地球の自転と一緒にぐるぐる回る座標系。GPSの位置と同じです。
//   ECI から ECEF への変換には「グリニッジ恒星時 (GMST: 今地球が何度自転しているか)」を使います。
// - Topocentric 水平座標系 (ENU): 観測者（ベランダのアンテナ）から見た「東(East)・北(North)・天頂(Up)」。
//   ここから 仰角 (Elevation) と 方位角 (Azimuth) を三角関数で算出します。
// =============================================================================

/// 信号伝送・変調方式
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalType {
    /// NOAA APT: 2.4kHz AM副搬送波 + FM主搬送波 (11025Hz アナログ音声)
    Apt,
    /// Meteor-M LRPT: 72k/80k QPSK デジタル変調 (広帯域IQストリーム)
    Lrpt,
    /// CubeSat SSDV: カメラ画像分割パケット送信 (生IQストリーム)
    CubeSatSsdv,
    /// CubeSat SSTV: アナログスロースキャンTV画像 (生IQストリーム)
    CubeSatSstv,
    /// CubeSat Telemetry: BPSK/AFSK テレメトリデータ (生IQストリーム)
    CubeSatTelemetry,
    /// CubeSat Morse: CWモールス符号 (狭帯域IQまたはオーディオ)
    MorseCw,
    /// ISS SSTV: 国際宇宙ステーションからのカラー画像 (Robot36 / Martin1 等)
    IssSstv,
}

impl SignalType {
    pub fn name(&self) -> &'static str {
        match self {
            SignalType::Apt => "NOAA APT (アナログ)",
            SignalType::Lrpt => "Meteor LRPT (デジタルQPSK)",
            SignalType::CubeSatSsdv => "CubeSat SSDV (カメラ画像)",
            SignalType::CubeSatSstv => "CubeSat SSTV (カメラ画像)",
            SignalType::CubeSatTelemetry => "CubeSat Telemetry (テレメトリ)",
            SignalType::MorseCw => "CubeSat Morse (モールスCW)",
            SignalType::IssSstv => "ISS SSTV (宇宙ステーション画像)",
        }
    }

    pub fn from_str_type(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "camerasstv" | "sstv" | "cubesatsstv" => SignalType::CubeSatSstv,
            "isssstv" | "iss" => SignalType::IssSstv,
            "ssdvcamera" | "ssdv" => SignalType::CubeSatSsdv,
            "morsecw" | "morse" | "cw" => SignalType::MorseCw,
            "lrpt" => SignalType::Lrpt,
            "apt" => SignalType::Apt,
            _ => SignalType::CubeSatTelemetry,
        }
    }

    /// 受信録音時の方式（FM音声 vs 生IQベースバンド）
    pub fn is_raw_iq(&self) -> bool {
        match self {
            SignalType::Apt | SignalType::IssSstv => false,
            SignalType::Lrpt
            | SignalType::CubeSatSsdv
            | SignalType::CubeSatSstv
            | SignalType::CubeSatTelemetry
            | SignalType::MorseCw => true,
        }
    }
}

/// 対象衛星の情報（TLE・受信周波数・信号種別）
#[derive(Debug, Clone)]
pub struct SatelliteInfo {
    pub name: String,
    pub norad_id: u32,
    pub frequency_hz: u64,
    pub signal_type: SignalType,
    pub line1: String,
    pub line2: String,
}

/// 検出された衛星通過イベント（パス）
#[derive(Debug, Clone)]
pub struct SatellitePass {
    pub satellite_name: String,
    pub frequency_hz: u64,
    pub signal_type: SignalType,
    pub aos: DateTime<Utc>,           // Acquisition of Signal: 観測開始時刻（仰角閾値超え）
    pub los: DateTime<Utc>,           // Loss of Signal: 観測終了時刻（地平線下へ沈む）
    pub max_elevation_deg: f64,       // ピーク仰角（アンテナに最も電波が強く入る瞬間）
    pub peak_azimuth_deg: f64,        // ピーク時の方位角 (0度=北, 90度=東, 180度=南, 270度=西)
}

/// 方位角（度）を16方位の日本語方角名に変換
pub fn azimuth_to_direction(az_deg: f64) -> &'static str {
    let normalized = az_deg.rem_euclid(360.0);
    match normalized {
        a if (348.75..=360.0).contains(&a) || (0.0..11.25).contains(&a) => "北 (N)",
        a if (11.25..33.75).contains(&a) => "北北東 (NNE)",
        a if (33.75..56.25).contains(&a) => "北東 (NE)",
        a if (56.25..78.75).contains(&a) => "東北東 (ENE)",
        a if (78.75..101.25).contains(&a) => "東 (E)",
        a if (101.25..123.75).contains(&a) => "東南東 (ESE)",
        a if (123.75..146.25).contains(&a) => "南東 (SE)",
        a if (146.25..168.75).contains(&a) => "南南東 (SSE)",
        a if (168.75..191.25).contains(&a) => "南 (S)",
        a if (191.25..213.75).contains(&a) => "南南西 (SSW)",
        a if (213.75..236.25).contains(&a) => "南西 (SW)",
        a if (236.25..258.75).contains(&a) => "西南西 (WSW)",
        a if (258.75..281.25).contains(&a) => "西 (W)",
        a if (281.25..303.75).contains(&a) => "西北西 (WNW)",
        a if (303.75..326.25).contains(&a) => "北西 (NW)",
        _ => "北北西 (NNW)",
    }
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
        // TLE 行から SGP4 軌道要素構造体を生成
        let elements = Elements::from_tle(
            Some(sat.name.clone()),
            sat.line1.as_bytes(),
            sat.line2.as_bytes(),
        )
        .map_err(|e| anyhow::anyhow!("TLEのパースに失敗しました: {:?}", e))?;

        let constants = Constants::from_elements(&elements)
            .map_err(|e| anyhow::anyhow!("SGP4 Constantsの初期化に失敗しました: {:?}", e))?;

        // 観測地点（ベランダ）のECEF座標 (km) を事前計算
        let obs_ecef = geodetic_to_ecef(observer.latitude, observer.longitude, observer.altitude_m);

        let mut passes = Vec::new();
        let mut in_pass = false;
        let mut current_aos = start_time;
        let mut max_el = 0.0f64;
        let mut max_az = 0.0f64;

        // 30秒刻みで未来の軌道をサンプリング（ポーリングスキャン）
        let step_seconds = 30i64;
        let total_steps = (duration_hours as i64 * 3600) / step_seconds;

        for i in 0..=total_steps {
            let t = start_time + Duration::seconds(i * step_seconds);
            let (el, az) = calculate_topo_pos(&elements, &constants, &obs_ecef, observer.latitude, observer.longitude, t);

            if el >= min_el_deg {
                if !in_pass {
                    // 仰角が閾値を上に突き抜けた瞬間 (AOS)
                    in_pass = true;
                    current_aos = t;
                    max_el = el;
                    max_az = az;
                } else if el > max_el {
                    // ピーク仰角と方位角を更新
                    max_el = el;
                    max_az = az;
                }
            } else if in_pass {
                // 仰角が閾値を下回って見えなくなった瞬間 (LOS)
                in_pass = false;
                passes.push(SatellitePass {
                    satellite_name: sat.name.clone(),
                    frequency_hz: sat.frequency_hz,
                    signal_type: sat.signal_type,
                    aos: current_aos,
                    los: t,
                    max_elevation_deg: max_el,
                    peak_azimuth_deg: max_az,
                });
                max_el = 0.0;
                max_az = 0.0;
            }
        }

        // スキャン境界でパスが継続していた場合の救済処理
        if in_pass {
            passes.push(SatellitePass {
                satellite_name: sat.name.clone(),
                frequency_hz: sat.frequency_hz,
                signal_type: sat.signal_type,
                aos: current_aos,
                los: start_time + Duration::hours(duration_hours as i64),
                max_elevation_deg: max_el,
                peak_azimuth_deg: max_az,
            });
        }

        Ok(passes)
    }

    /// 複数衛星の通過パスを予測し、時系列順にソート＆重複調整して返す
    /// 【言語対比】
    /// - `Vec::extend`: Python の `list.extend` や Go の `append(slice, items...)` に相当。
    /// - `sort_by_key`: Python の `list.sort(key=lambda p: p.aos)` や Go の `sort.Slice` に相当。
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

        // AOS（通過開始時刻）の昇順に時系列ソート
        all_passes.sort_by_key(|p| p.aos);

        // 重複調停アルゴリズム:
        // アンテナ（SDR）は1台しかないため、2つの衛星が同時に空に現れた場合は
        // 「最大仰角が高い方（電波強度が強く高品質に受信できる方）」を優先採用する
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

/// 3行フォーマットのTLEテキストをパースして NORAD ID をキーとしてマップに格納
pub fn parse_3line_tles(
    text: &str,
    out_map: &mut std::collections::HashMap<u32, (String, String, String)>,
) {
    let lines: Vec<&str> = text
        .lines()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect();

    let mut i = 0;
    while i + 2 < lines.len() {
        if lines[i + 1].starts_with("1 ") && lines[i + 2].starts_with("2 ") {
            let sat_name = lines[i].to_string();
            let line1 = lines[i + 1].to_string();
            let line2 = lines[i + 2].to_string();

            // Line 2 の文字インデックス 2..7 から NORAD ID を抽出 (例: "2 25544 ...")
            if line2.len() >= 7 {
                if let Ok(norad_id) = line2[2..7].trim().parse::<u32>() {
                    out_map.insert(norad_id, (sat_name, line1, line2));
                }
            }
            i += 3;
        } else {
            i += 1;
        }
    }
}

/// CelesTrak から対象の全衛星（気象衛星、キューブサット、ISS等）の TLE を一括・個別取得
pub async fn fetch_all_tles(
    client: &Client,
    satellites_config: &SatellitesConfig,
) -> Result<Vec<SatelliteInfo>> {
    let mut targets: Vec<(String, u32, u64, SignalType)> = Vec::new();

    // 1. 気象衛星 Meteor-M (ロシア極軌道 LRPT)
    if satellites_config.is_meteor_enabled() {
        targets.push(("Meteor-M N2-3".to_string(), 57166, 137_900_000, SignalType::Lrpt));
        targets.push(("Meteor-M N2-4".to_string(), 59051, 137_900_000, SignalType::Lrpt));
    }

    // 2. 気象衛星 NOAA (米国極軌道 APT - 停波)
    if satellites_config.is_noaa_enabled() {
        targets.push(("NOAA 15".to_string(), 25338, 137_620_000, SignalType::Apt));
        targets.push(("NOAA 18".to_string(), 28654, 137_912_500, SignalType::Apt));
        targets.push(("NOAA 19".to_string(), 33591, 137_100_000, SignalType::Apt));
    }

    // 3. 国際宇宙ステーション (ISS SSTV/FM)
    if satellites_config.iss.enabled {
        targets.push((
            "ISS (ZARYA)".to_string(),
            satellites_config.iss.norad_id,
            satellites_config.iss.freq,
            SignalType::IssSstv,
        ));
    }

    // 4. キューブサット (超小型衛星)
    if satellites_config.cubesats.enabled {
        for t in &satellites_config.cubesats.targets {
            let sig_type = SignalType::from_str_type(&t.r#type);
            targets.push((t.name.clone(), t.norad_id, t.freq, sig_type));
        }
    }

    if targets.is_empty() {
        info!("追尾対象の衛星が設定されていません");
        return Ok(Vec::new());
    }

    // CelesTrak からグループ TLE を取得してメモリ上にインデックス化
    let mut tle_db: std::collections::HashMap<u32, (String, String, String)> =
        std::collections::HashMap::new();

    // 4-1. 気象衛星グループ TLE
    if satellites_config.is_meteor_enabled() || satellites_config.is_noaa_enabled() {
        let weather_url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle";
        if let Ok(resp) = client.get(weather_url).send().await {
            if let Ok(text) = resp.text().await {
                parse_3line_tles(&text, &mut tle_db);
            }
        }
    }

    // 4-2. アマチュア衛星グループ TLE (CubeSat, ISS)
    if satellites_config.cubesats.enabled || satellites_config.iss.enabled {
        let amateur_url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle";
        if let Ok(resp) = client.get(amateur_url).send().await {
            if let Ok(text) = resp.text().await {
                parse_3line_tles(&text, &mut tle_db);
            }
        }
    }

    let mut results = Vec::new();
    for (name, norad_id, freq, sig_type) in targets {
        // グループ TLE から探索
        if let Some((_tle_name, line1, line2)) = tle_db.get(&norad_id) {
            results.push(SatelliteInfo {
                name,
                norad_id,
                frequency_hz: freq,
                signal_type: sig_type,
                line1: line1.clone(),
                line2: line2.clone(),
            });
        } else {
            // グループに含まれていない場合は個別 CATNR クエリで取得
            let url = format!(
                "https://celestrak.org/NORAD/elements/gp.php?CATNR={}&FORMAT=tle",
                norad_id
            );
            info!("TLE 個別取得中: {} (NORAD ID: {})", name, norad_id);
            if let Ok(resp) = client.get(&url).send().await {
                if let Ok(text) = resp.text().await {
                    let lines: Vec<&str> = text
                        .lines()
                        .map(|s| s.trim())
                        .filter(|s| !s.is_empty())
                        .collect();
                    if lines.len() >= 3 && lines[1].starts_with("1 ") && lines[2].starts_with("2 ") {
                        results.push(SatelliteInfo {
                            name,
                            norad_id,
                            frequency_hz: freq,
                            signal_type: sig_type,
                            line1: lines[1].to_string(),
                            line2: lines[2].to_string(),
                        });
                    }
                }
            }
        }
    }

    info!("合計 {} 機の衛星 TLE を読み込みました", results.len());
    Ok(results)
}

/// 下位互換用エイリアス
pub async fn fetch_weather_tles(
    client: &Client,
    satellites_config: &SatellitesConfig,
) -> Result<Vec<SatelliteInfo>> {
    fetch_all_tles(client, satellites_config).await
}

/// 観測地（緯度・経度・標高）の WGS84 楕円体における ECEF 直交座標 (km) を算出
fn geodetic_to_ecef(lat_deg: f64, lon_deg: f64, alt_m: f64) -> [f64; 3] {
    let lat = lat_deg.to_radians();
    let lon = lon_deg.to_radians();
    let alt_km = alt_m / 1000.0;

    let a = 6378.137; // 地球赤道半径 (km)
    let f = 1.0 / 298.257223563; // 地球の扁平率 (赤道が膨らんでいる度合い)
    let e2 = f * (2.0 - f); // 第一離心率の2乗

    // 卯酉線（ぼうゆうせん）曲率半径
    let n = a / (1.0 - e2 * lat.sin().powi(2)).sqrt();

    let x = (n + alt_km) * lat.cos() * lon.cos();
    let y = (n + alt_km) * lat.cos() * lon.sin();
    let z = (n * (1.0 - e2) + alt_km) * lat.sin();

    [x, y, z]
}

/// 指定時刻における衛星の仰角 (度) と方位角 (度) を計算
/// 戻り値: (elevation_deg, azimuth_deg)
fn calculate_topo_pos(
    elements: &Elements,
    constants: &Constants,
    obs_ecef: &[f64; 3],
    lat_deg: f64,
    lon_deg: f64,
    t: DateTime<Utc>,
) -> (f64, f64) {
    // TLE エポック（起点時刻）からの経過時間 (分) を計算
    let epoch_dt = DateTime::<Utc>::from_naive_utc_and_offset(elements.datetime, Utc);
    let diff = t.signed_duration_since(epoch_dt);
    let minutes_since_epoch = diff.num_milliseconds() as f64 / 60_000.0;

    // SGP4 モデルで衛星の位置 (ECI座標系 [km]) を推算
    let prediction = match constants.propagate(minutes_since_epoch) {
        Ok(p) => p,
        Err(_) => return (-90.0, 0.0), // 軌道崩壊または計算不能時は地平線下扱い
    };

    let sat_eci = [
        prediction.position[0],
        prediction.position[1],
        prediction.position[2],
    ];

    // グリニッジ平均恒星時 (GMST) 角 [rad] を計算し、地球の自転分だけ回転
    let gmst = calculate_gmst(t);
    let cos_g = gmst.cos();
    let sin_g = gmst.sin();
    let sat_ecef = [
        cos_g * sat_eci[0] + sin_g * sat_eci[1],
        -sin_g * sat_eci[0] + cos_g * sat_eci[1],
        sat_eci[2],
    ];

    // 観測地点（アンテナ）から衛星への相対ベクトル
    let rx = sat_ecef[0] - obs_ecef[0];
    let ry = sat_ecef[1] - obs_ecef[1];
    let rz = sat_ecef[2] - obs_ecef[2];

    // Topocentric 水平座標系 (East, North, Up) への変換
    let lat = lat_deg.to_radians();
    let lon = lon_deg.to_radians();

    let sin_lat = lat.sin();
    let cos_lat = lat.cos();
    let sin_lon = lon.sin();
    let cos_lon = lon.cos();

    // 東 (East), 北 (North), 天頂 (Up) 成分
    let east = -sin_lon * rx + cos_lon * ry;
    let north = -sin_lat * cos_lon * rx - sin_lat * sin_lon * ry + cos_lat * rz;
    let up = cos_lat * cos_lon * rx + cos_lat * sin_lon * ry + sin_lat * rz;

    // アンテナから衛星までの直線距離 (Range)
    let range = (rx.powi(2) + ry.powi(2) + rz.powi(2)).sqrt();
    if range < 1e-6 {
        return (-90.0, 0.0);
    }

    // 仰角: sin(elevation) = Up / Range
    let sin_el = up / range;
    let el_rad = sin_el.clamp(-1.0, 1.0).asin();
    let el_deg = el_rad.to_degrees();

    // 方位角: 北(0度)を基準とし、時計回りに東(90度)、南(180度)、西(270度)
    // az = atan2(East, North)
    let az_rad = east.atan2(north);
    let az_deg = az_rad.to_degrees().rem_euclid(360.0);

    (el_deg, az_deg)
}


/// 指定 UTC 日時におけるグリニッジ平均恒星時 (GMST) を算出 (rad)
/// IAU 1982 公式を用いて地球の自転角を求めます。
fn calculate_gmst(t: DateTime<Utc>) -> f64 {
    let ts = t.timestamp() as f64;
    let jd = (ts / 86400.0) + 2440587.5; // ユリウス日 (JD)
    let d = jd - 2451545.0;              // J2000.0 からの日数

    let gmst_deg = 280.46061837 + 360.98564736629 * d;
    let gmst_deg_norm = gmst_deg.rem_euclid(360.0);
    gmst_deg_norm.to_radians()
}
