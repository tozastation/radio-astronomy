use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::{Duration, Instant};

// =============================================================================
// ✈️ ADS-B 航空機監視モジュール (adsb)
// -----------------------------------------------------------------------------
// 【数学・信号処理・ジオフェンシング】
// 1. 球面大圏距離 (Haversine 公式):
//    地表の2点 (緯度・経度) 間の最短弧長を計算。極半径と赤道半径の平均値 R=6371.0km を
//    用いることで、青梅市周辺の航空機探知において誤差数メートル以内の高精度を達成。
// 2. 高度換算 (Feet -> Meter):
//    Mode S 拡張スキッターは航空気圧高度をフィート (ft) 単位で送信します。
//    1 ft = 0.3048 m によりメートルに換算し、地上駐機ノイズ ("ground") は 0m と判定。
// 3. 多重通知防止とキャッシュ (AdsbCache):
//    同一機体への重複通知を防ぐクールダウン (例: 30分) と、Planespotters.net (実機写真)
//    および hexdb.io (発着ルート) の外部 API レスポンスのインメモリキャッシュを統合。
// =============================================================================

/// 地球平均半径 (km)
const EARTH_RADIUS_KM: f64 = 6371.0;

/// 2つの緯度・経度 (度) 間の球面大圏距離を計算 (Haversine 公式)
pub fn haversine_distance_km(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let d_lat = (lat2 - lat1).to_radians();
    let d_lon = (lon2 - lon1).to_radians();

    let lat1_rad = lat1.to_radians();
    let lat2_rad = lat2.to_radians();

    let a = (d_lat / 2.0).sin().powi(2)
        + lat1_rad.cos() * lat2_rad.cos() * (d_lon / 2.0).sin().powi(2);
    let c = 2.0 * a.sqrt().atan2((1.0 - a).sqrt());

    EARTH_RADIUS_KM * c
}

/// readsb の alt_baro 値 (数値または "ground" 文字列) を高度 (m) に換算
pub fn parse_altitude_m(val: &serde_json::Value) -> Option<f64> {
    match val {
        serde_json::Value::Number(n) => n.as_f64().map(|ft| ft * 0.3048),
        serde_json::Value::String(s) if s.eq_ignore_ascii_case("ground") => Some(0.0),
        _ => None,
    }
}

/// readsb が出力する `aircraft.json` の1機体レコード
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AircraftRecord {
    pub hex: String,
    pub flight: Option<String>,
    pub alt_baro: Option<serde_json::Value>,
    pub gs: Option<f64>,
    pub track: Option<f64>,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
    pub seen: Option<f64>,
}

impl AircraftRecord {
    /// 前後の空白を除去したきれいなコールサインを取得
    pub fn clean_callsign(&self) -> Option<String> {
        self.flight
            .as_ref()
            .map(|f| f.trim().to_string())
            .filter(|f| !f.is_empty())
    }

    /// 高度 (メートル) を取得
    pub fn altitude_m(&self) -> Option<f64> {
        self.alt_baro.as_ref().and_then(parse_altitude_m)
    }

    /// 対地速度 (km/h) を取得 (1 knot = 1.852 km/h)
    pub fn speed_kmh(&self) -> Option<f64> {
        self.gs.map(|knot| knot * 1.852)
    }

    /// 有効な位置座標を持っているか
    pub fn has_position(&self) -> bool {
        self.lat.is_some() && self.lon.is_some()
    }
}

/// readsb の `aircraft.json` トップレベル構造体
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AircraftJson {
    pub now: f64,
    pub messages: Option<u64>,
    #[serde(default)]
    pub aircraft: Vec<AircraftRecord>,
}

/// 発着地ルート情報
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct FlightRoute {
    pub callsign: String,
    pub origin_iata: Option<String>,
    pub origin_name: Option<String>,
    pub destination_iata: Option<String>,
    pub destination_name: Option<String>,
}

/// Planespotters.net から取得した実機写真メタデータ
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct AircraftPhotoMeta {
    pub thumbnail_large: String,
    pub photographer: String,
    pub aircraft_type: Option<String>,
    pub airline_name: Option<String>,
}

/// 個別機体の追跡ステート
#[derive(Debug, Clone)]
struct TrackState {
    last_notified: Option<Instant>,
    min_dist_km: f64,
    last_seen: Instant,
}

/// ADS-B 近接監視用インメモリキャッシュ
#[derive(Debug, Clone)]
pub struct AdsbCache {
    cooldown: Duration,
    tracks: HashMap<String, TrackState>,
    photos: HashMap<String, Option<AircraftPhotoMeta>>,
    routes: HashMap<String, Option<FlightRoute>>,
}

impl AdsbCache {
    pub fn new(cooldown_minutes: u64) -> Self {
        Self {
            cooldown: Duration::from_secs(cooldown_minutes * 60),
            tracks: HashMap::new(),
            photos: HashMap::new(),
            routes: HashMap::new(),
        }
    }

    /// 通知すべきか判定 (未通知またはクールダウン経過)
    pub fn should_notify(&mut self, hex: &str, dist_km: f64) -> bool {
        let now = Instant::now();
        if let Some(state) = self.tracks.get_mut(hex) {
            state.last_seen = now;
            if dist_km < state.min_dist_km {
                state.min_dist_km = dist_km;
            }
            if let Some(last) = state.last_notified {
                if now.duration_since(last) < self.cooldown {
                    return false;
                }
            }
            true
        } else {
            self.tracks.insert(
                hex.to_string(),
                TrackState {
                    last_notified: None,
                    min_dist_km: dist_km,
                    last_seen: now,
                },
            );
            true
        }
    }

    /// 通知完了マーク
    pub fn mark_notified(&mut self, hex: &str, dist_km: f64) {
        let now = Instant::now();
        if let Some(state) = self.tracks.get_mut(hex) {
            state.last_notified = Some(now);
            state.min_dist_km = dist_km;
            state.last_seen = now;
        } else {
            self.tracks.insert(
                hex.to_string(),
                TrackState {
                    last_notified: Some(now),
                    min_dist_km: dist_km,
                    last_seen: now,
                },
            );
        }
    }

    pub fn get_photo(&self, hex: &str) -> Option<&Option<AircraftPhotoMeta>> {
        self.photos.get(hex)
    }

    pub fn set_photo(&mut self, hex: String, photo: Option<AircraftPhotoMeta>) {
        self.photos.insert(hex, photo);
    }

    pub fn get_route(&self, callsign: &str) -> Option<&Option<FlightRoute>> {
        self.routes.get(callsign)
    }

    pub fn set_route(&mut self, callsign: String, route: Option<FlightRoute>) {
        self.routes.insert(callsign, route);
    }

    /// 1時間以上見失った古いトラッキングステートをパージ
    pub fn cleanup_stale(&mut self) {
        let now = Instant::now();
        let timeout = Duration::from_secs(3600);
        self.tracks
            .retain(|_, state| now.duration_since(state.last_seen) < timeout);
    }
}

/// hexdb.io の JSON レスポンスからルート情報をパース
pub fn parse_hexdb_route(callsign: &str, json_str: &str) -> Option<FlightRoute> {
    let v: serde_json::Value = serde_json::from_str(json_str).ok()?;
    let origin_iata = v
        .get("origin")
        .and_then(|o| o.get("iata"))
        .and_then(|s| s.as_str())
        .map(|s| s.to_string());
    let origin_name = v
        .get("origin")
        .and_then(|o| o.get("name"))
        .and_then(|s| s.as_str())
        .map(|s| s.to_string());
    let destination_iata = v
        .get("destination")
        .and_then(|d| d.get("iata"))
        .and_then(|s| s.as_str())
        .map(|s| s.to_string());
    let destination_name = v
        .get("destination")
        .and_then(|d| d.get("name"))
        .and_then(|s| s.as_str())
        .map(|s| s.to_string());

    if origin_iata.is_none() && destination_iata.is_none() {
        return None;
    }

    Some(FlightRoute {
        callsign: callsign.to_string(),
        origin_iata,
        origin_name,
        destination_iata,
        destination_name,
    })
}

/// Planespotters.net の JSON レスポンスから実機写真メタデータをパース
pub fn parse_planespotters_photo(json_str: &str) -> Option<AircraftPhotoMeta> {
    let v: serde_json::Value = serde_json::from_str(json_str).ok()?;
    let photos = v.get("photos")?.as_array()?;
    let first = photos.first()?;

    let thumbnail_large = first
        .get("thumbnail_large")
        .and_then(|t| t.get("src"))
        .and_then(|s| s.as_str())?
        .to_string();

    let photographer = first
        .get("photographer")
        .and_then(|s| s.as_str())
        .unwrap_or("Unknown Photographer")
        .to_string();

    let aircraft_type = first
        .get("aircraft_type")
        .and_then(|s| s.as_str())
        .map(|s| s.to_string());

    let airline_name = first
        .get("airline")
        .and_then(|a| a.get("name"))
        .and_then(|s| s.as_str())
        .map(|s| s.to_string());

    Some(AircraftPhotoMeta {
        thumbnail_large,
        photographer,
        aircraft_type,
        airline_name,
    })
}

/// hexdb.io API からフライト発着ルートを取得
pub async fn fetch_flight_route(client: &reqwest::Client, callsign: &str) -> Option<FlightRoute> {
    let url = format!("https://hexdb.io/api/v1/route/icao/{}", callsign);
    let resp = client
        .get(&url)
        .header("User-Agent", "radio-astronomy-ground-station/0.1.0")
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .ok()?;

    if !resp.status().is_success() {
        return None;
    }

    let text = resp.text().await.ok()?;
    parse_hexdb_route(callsign, &text)
}

/// Planespotters.net API から実機写真メタデータを取得
pub async fn fetch_aircraft_photo(client: &reqwest::Client, hex: &str) -> Option<AircraftPhotoMeta> {
    let url = format!("https://api.planespotters.net/pub/photos/hex/{}", hex);
    let resp = client
        .get(&url)
        .header(
            "User-Agent",
            "radio-astronomy-ground-station/0.1.0 (https://github.com/tozastation/radio-astronomy)",
        )
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .ok()?;

    if !resp.status().is_success() {
        return None;
    }

    let text = resp.text().await.ok()?;
    parse_planespotters_photo(&text)
}

