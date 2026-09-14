use anyhow::Result;
use log::{info, warn};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::config::Config;
use crate::discord::{AircraftAlert, DiscordClient};
use crate::voicevox::VoicevoxClient;

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

/// 指定されたベースURLから一般的な readsb / tar1090 / dump1090 の JSON エンドポイント候補リストを生成
pub fn generate_candidate_data_urls(base_url: &str) -> Vec<String> {
    let mut urls = vec![base_url.to_string()];
    if let Ok(mut parsed) = reqwest::Url::parse(base_url) {
        let default_paths = [
            "/data/aircraft.json",
            "/tar1090/data/aircraft.json",
            "/aircraft.json",
            "/run/readsb/aircraft.json",
        ];
        for p in default_paths {
            parsed.set_path(p);
            let s = parsed.to_string();
            if !urls.contains(&s) {
                urls.push(s);
            }
        }
    }
    urls
}

/// hexdb.io API からフライト発着ルートを取得
pub async fn fetch_flight_route(client: &reqwest::Client, callsign: &str) -> Option<FlightRoute> {
    let url = format!("https://hexdb.io/api/v1/route/icao/{}", callsign);
    let resp = client
        .get(&url)
        .header("User-Agent", "radio-astronomy-ground-station/0.1.0")
        .timeout(Duration::from_secs(3))
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
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .ok()?;

    if !resp.status().is_success() {
        return None;
    }

    let text = resp.text().await.ok()?;
    parse_planespotters_photo(&text)
}

/// ずんだもん発話用テキストを生成
pub fn build_voice_text(alert: &crate::discord::AircraftAlert) -> String {
    let alt_m = alert.altitude_m.round() as i64;
    let airline = alert.airline.as_deref().unwrap_or("");
    let flight = &alert.callsign;
    let ac_type = alert.aircraft_type.as_deref().unwrap_or("飛行機");

    match (&alert.origin, &alert.destination) {
        (Some(orig), Some(dest)) => {
            format!(
                "{}発、{}行きの{}{}便、{}が、高度{}メートルで上空を通過中なのだ！",
                orig, dest, airline, flight, ac_type, alt_m
            )
        }
        _ => {
            if !airline.is_empty() {
                format!(
                    "{}の{}、{}便が、高度{}メートルで上空を通過中なのだ！",
                    airline, ac_type, flight, alt_m
                )
            } else {
                format!(
                    "{}便、{}が、高度{}メートルで上空を通過中なのだ！",
                    flight, ac_type, alt_m
                )
            }
        }
    }
}

/// ADS-B 常駐近接監視ループ
pub async fn run_adsb_monitor(
    config: Config,
    discord: Arc<DiscordClient>,
    voice: Arc<VoicevoxClient>,
) -> Result<()> {
    let http_client = reqwest::Client::new();
    let mut cache = AdsbCache::new(config.adsb.cooldown_minutes);
    let poll_interval = Duration::from_secs(config.adsb.poll_interval_secs.max(1));

    let candidate_urls = generate_candidate_data_urls(&config.adsb.data_url);
    let mut active_url: Option<String> = None;

    info!(
        "✈️ ADS-B 航空機近接監視ループを開始しました (設定URL: {}, 判定半径: {:.1}km, 探索候補: {} 件)",
        config.adsb.data_url, config.adsb.max_distance_km, candidate_urls.len()
    );

    let mut last_cleanup = Instant::now();
    let mut last_heartbeat = Instant::now();
    let mut last_error_warn = Instant::now() - Duration::from_secs(60);

    loop {
        tokio::select! {
            _ = tokio::time::sleep(poll_interval) => {},
            _ = tokio::signal::ctrl_c() => {
                info!("ADS-B 監視ループが停止シグナルを受信しました");
                break;
            }
        }

        // 定期キャッシュパージ (10分ごと)
        if last_cleanup.elapsed() > Duration::from_secs(600) {
            cache.cleanup_stale();
            last_cleanup = Instant::now();
        }

        // 1. readsb の aircraft.json を取得 (自動探索 & フォールバック)
        let mut fetched_json: Option<(AircraftJson, String)> = None;
        let urls_to_try = if let Some(ref act) = active_url {
            let mut list = vec![act.clone()];
            for u in &candidate_urls {
                if u != act {
                    list.push(u.clone());
                }
            }
            list
        } else {
            candidate_urls.clone()
        };

        let mut last_err_msg = String::new();

        for url in &urls_to_try {
            match http_client
                .get(url)
                .timeout(Duration::from_secs(2))
                .send()
                .await
            {
                Ok(resp) if resp.status().is_success() => {
                    match resp.json::<AircraftJson>().await {
                        Ok(json) => {
                            if active_url.as_ref() != Some(url) {
                                info!("📡 ADS-B 有効なエンドポイントを検出・確定しました: {}", url);
                                active_url = Some(url.clone());
                            }
                            fetched_json = Some((json, url.clone()));
                            break;
                        }
                        Err(e) => {
                            last_err_msg = format!("{}: JSONパース失敗 ({})", url, e);
                        }
                    }
                }
                Ok(resp) => {
                    last_err_msg = format!("{}: HTTP {}", url, resp.status());
                }
                Err(e) => {
                    last_err_msg = format!("{}: 通信エラー ({})", url, e);
                }
            }
        }

        let aircraft_json = match fetched_json {
            Some((json, _)) => json,
            None => {
                active_url = None;
                if last_error_warn.elapsed() >= Duration::from_secs(30) {
                    warn!(
                        "⚠️ ADS-B エンドポイントに接続できません (最新試行: {})。dump1090 / readsb / Ultrafeeder コンテナが起動しているか確認してください",
                        last_err_msg
                    );
                    last_error_warn = Instant::now();
                }
                continue;
            }
        };

        // 2. 定期ハートビート / レーダーステータスログ (30秒ごと)
        if last_heartbeat.elapsed() >= Duration::from_secs(30) {
            last_heartbeat = Instant::now();
            let total = aircraft_json.aircraft.len();
            let with_pos: Vec<_> = aircraft_json.aircraft.iter().filter(|a| a.has_position()).collect();

            if with_pos.is_empty() {
                info!("📡 ADS-B レーダー状況: 捕捉 {} 機 (位置確定 0 機) | 電波待機中...", total);
            } else {
                let mut min_dist = f64::INFINITY;
                let mut nearest_callsign = String::new();
                let mut nearest_alt = 0.0;
                for ac in &with_pos {
                    if let (Some(la), Some(lo)) = (ac.lat, ac.lon) {
                        let d = haversine_distance_km(config.observer.latitude, config.observer.longitude, la, lo);
                        if d < min_dist {
                            min_dist = d;
                            nearest_callsign = ac.clean_callsign().unwrap_or_else(|| format!("HEX-{}", ac.hex));
                            nearest_alt = ac.altitude_m().unwrap_or(0.0);
                        }
                    }
                }
                info!(
                    "📡 ADS-B レーダー状況: 捕捉 {} 機 (位置確定 {} 機) | 最接近: {} ({:.1}km, 高度 {:.0}m)",
                    total, with_pos.len(), nearest_callsign, min_dist, nearest_alt
                );
            }
        }

        // 3. 各機体の接近判定
        for ac in &aircraft_json.aircraft {
            let (lat, lon) = match (ac.lat, ac.lon) {
                (Some(la), Some(lo)) => (la, lo),
                _ => continue,
            };

            let alt_m = match ac.altitude_m() {
                Some(a) => a,
                None => continue,
            };

            // 高度範囲チェック
            if alt_m < config.adsb.min_altitude_m || alt_m > config.adsb.max_altitude_m {
                continue;
            }

            // 自宅座標からの距離計算
            let dist_km = haversine_distance_km(
                config.observer.latitude,
                config.observer.longitude,
                lat,
                lon,
            );

            // ジオフェンス判定
            if dist_km > config.adsb.max_distance_km {
                continue;
            }

            // クールダウン & 通知済みチェック
            if !cache.should_notify(&ac.hex, dist_km) {
                continue;
            }

            // 初回検知: 即時マークして多重通知防止
            cache.mark_notified(&ac.hex, dist_km);

            let callsign = ac.clean_callsign().unwrap_or_else(|| format!("HEX-{}", &ac.hex));
            info!(
                "🎯 自宅上空に航空機が接近中！ 便名: {}, 距離: {:.1}km, 高度: {:.0}m",
                callsign, dist_km, alt_m
            );

            // ルート情報と実機写真メタデータの並列取得 (キャッシュ優先)
            let callsign_clone = callsign.clone();
            let hex_clone = ac.hex.clone();

            let cached_route = cache.get_route(&callsign).cloned();
            let cached_photo = cache.get_photo(&ac.hex).cloned();

            let (route, photo) = tokio::join!(
                async {
                    if !config.adsb.fetch_routes {
                        return None;
                    }
                    if let Some(r) = cached_route {
                        return r;
                    }
                    fetch_flight_route(&http_client, &callsign_clone).await
                },
                async {
                    if !config.adsb.fetch_photos {
                        return None;
                    }
                    if let Some(p) = cached_photo {
                        return p;
                    }
                    fetch_aircraft_photo(&http_client, &hex_clone).await
                }
            );

            if config.adsb.fetch_routes {
                cache.set_route(callsign.clone(), route.clone());
            }
            if config.adsb.fetch_photos {
                cache.set_photo(ac.hex.clone(), photo.clone());
            }

            let alert = AircraftAlert {
                icao_hex: ac.hex.clone(),
                callsign: callsign.clone(),
                airline: photo.as_ref().and_then(|p| p.airline_name.clone()),
                aircraft_type: photo.as_ref().and_then(|p| p.aircraft_type.clone()),
                origin: route.as_ref().and_then(|r| {
                    match (&r.origin_name, &r.origin_iata) {
                        (Some(name), Some(iata)) => Some(format!("{} ({})", name, iata)),
                        (Some(name), None) => Some(name.clone()),
                        (None, Some(iata)) => Some(iata.clone()),
                        (None, None) => None,
                    }
                }),
                destination: route.as_ref().and_then(|r| {
                    match (&r.destination_name, &r.destination_iata) {
                        (Some(name), Some(iata)) => Some(format!("{} ({})", name, iata)),
                        (Some(name), None) => Some(name.clone()),
                        (None, Some(iata)) => Some(iata.clone()),
                        (None, None) => None,
                    }
                }),
                altitude_m: alt_m,
                speed_kmh: ac.speed_kmh().unwrap_or(0.0),
                distance_km: dist_km,
                photo_url: photo.as_ref().map(|p| p.thumbnail_large.clone()),
                photographer: photo.as_ref().map(|p| p.photographer.clone()),
                tar1090_url: config.adsb.tar1090_url.clone(),
            };

            // Discord 通知
            if config.adsb.discord_alert {
                let discord_clone = discord.clone();
                let alert_clone = alert.clone();
                tokio::spawn(async move {
                    if let Err(e) = discord_clone.send_aircraft_alert(&alert_clone).await {
                        warn!("Discord 航空機通知エラー: {}", e);
                    }
                });
            }

            // VOICEVOX 発話
            if config.adsb.voice_alert {
                let voice_clone = voice.clone();
                let speech = build_voice_text(&alert);
                tokio::spawn(async move {
                    if let Err(e) = voice_clone.speak(&speech).await {
                        warn!("VOICEVOX 航空機発話エラー: {}", e);
                    }
                });
            }
        }
    }

    info!("ADS-B 航空機監視ループを正常終了しました");
    Ok(())
}

/// ADS-B 航空機監視・実機写真・Discord/VOICEVOX通知の単体疎通テスト
pub async fn test_adsb_alert(config: &Config) -> Result<()> {
    println!("✈️ ADS-B 航空機監視テストを実行中...");
    let http_client = reqwest::Client::new();
    let discord = Arc::new(DiscordClient::new(config.discord.clone()));
    let voice = Arc::new(VoicevoxClient::new(config.voicevox.clone()));

    // 1. readsb から実機データの取得を試みる (候補URLを順次探索)
    let candidate_urls = generate_candidate_data_urls(&config.adsb.data_url);
    let mut candidate_alert: Option<AircraftAlert> = None;
    let mut found_json: Option<(AircraftJson, String)> = None;

    for url in &candidate_urls {
        if let Ok(resp) = http_client
            .get(url)
            .timeout(Duration::from_secs(2))
            .send()
            .await
        {
            if resp.status().is_success() {
                if let Ok(json) = resp.json::<AircraftJson>().await {
                    println!("📡 readsb エンドポイント確認 ({}): 受信機体数 {} 機", url, json.aircraft.len());
                    found_json = Some((json, url.clone()));
                    break;
                }
            }
        }
    }

    if let Some((json, url)) = found_json {
        let mut closest: Option<(&AircraftRecord, f64)> = None;
                for ac in &json.aircraft {
                    if let (Some(la), Some(lo)) = (ac.lat, ac.lon) {
                        let d = haversine_distance_km(
                            config.observer.latitude,
                            config.observer.longitude,
                            la,
                            lo,
                        );
                        if closest.as_ref().map_or(true, |(_, min_d)| d < *min_d) {
                            closest = Some((ac, d));
                        }
                    }
                }

                if let Some((ac, dist)) = closest {
                    let callsign = ac.clean_callsign().unwrap_or_else(|| format!("HEX-{}", ac.hex));
                    println!("🎯 最接近機体を検出: 便名: {}, 距離: {:.1}km (データソース: {})", callsign, dist, url);
                    let route = fetch_flight_route(&http_client, &callsign).await;
                    let photo = fetch_aircraft_photo(&http_client, &ac.hex).await;
                    candidate_alert = Some(AircraftAlert {
                        icao_hex: ac.hex.clone(),
                        callsign: callsign.clone(),
                        airline: photo.as_ref().and_then(|p| p.airline_name.clone()),
                        aircraft_type: photo.as_ref().and_then(|p| p.aircraft_type.clone()),
                        origin: route.as_ref().and_then(|r| r.origin_iata.clone()),
                        destination: route.as_ref().and_then(|r| r.destination_iata.clone()),
                        altitude_m: ac.altitude_m().unwrap_or(5000.0),
                        speed_kmh: ac.speed_kmh().unwrap_or(750.0),
                        distance_km: dist,
                        photo_url: photo.as_ref().map(|p| p.thumbnail_large.clone()),
                        photographer: photo.as_ref().map(|p| p.photographer.clone()),
                        tar1090_url: config.adsb.tar1090_url.clone(),
                });
            }
        }

    // readsb に機体がいなかった場合、またはエンドポイント未起動の場合はモックデータでテスト
    let alert = candidate_alert.unwrap_or_else(|| {
        println!("ℹ️ 現在ADS-B電波圏内に機体がないため、サンプル機体（ANA247便/B787）でテストします");
        AircraftAlert {
            icao_hex: "86786c".to_string(),
            callsign: "ANA247".to_string(),
            airline: Some("全日本空輸".to_string()),
            aircraft_type: Some("Boeing 787-8 Dreamliner".to_string()),
            origin: Some("羽田 (HND)".to_string()),
            destination: Some("福岡 (FUK)".to_string()),
            altitude_m: 5200.0,
            speed_kmh: 780.0,
            distance_km: 3.4,
            photo_url: Some(
                "https://cdn.planespotters.net/photo/498000/original/planespotters_498305_4a2c91b5bf_o.jpg"
                    .to_string(),
            ),
            photographer: Some("John Doe".to_string()),
            tar1090_url: config.adsb.tar1090_url.clone(),
        }
    });

    println!("=================================================================");
    println!("✈️ テスト通知内容");
    println!("  便名:       {}", alert.callsign);
    println!("  航空会社:   {}", alert.airline.as_deref().unwrap_or("不明"));
    println!("  機種:       {}", alert.aircraft_type.as_deref().unwrap_or("不明"));
    println!(
        "  ルート:     {} ➜ {}",
        alert.origin.as_deref().unwrap_or("不明"),
        alert.destination.as_deref().unwrap_or("不明")
    );
    println!("  高度:       {:.0} m", alert.altitude_m);
    println!("  最接近距離: {:.1} km", alert.distance_km);
    println!("  実機写真:   {}", alert.photo_url.as_deref().unwrap_or("なし"));
    println!("=================================================================");

    if config.adsb.discord_alert {
        println!("📲 Discord 通知を送信中...");
        discord.send_aircraft_alert(&alert).await?;
        println!("✨ Discord 通知完了！");
    }

    if config.adsb.voice_alert {
        println!("🔊 ずんだもん発話中...");
        let text = build_voice_text(&alert);
        voice.speak(&text).await?;
        println!("✨ ずんだもん発話完了！ ({})", text);
    }

    Ok(())
}



