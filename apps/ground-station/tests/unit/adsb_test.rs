use ground_station::adsb::{
    haversine_distance_km, parse_altitude_m, AdsbCache, AircraftJson,
};

#[test]
fn test_haversine_distance_calculation() {
    // 観測地: 東京都青梅市 (北緯 35.7903, 東経 139.2584)
    let ome_lat = 35.7903;
    let ome_lon = 139.2584;

    // 羽田空港 (北緯 35.5494, 東経 139.7798)
    let hnd_lat = 35.5494;
    let hnd_lon = 139.7798;

    let dist = haversine_distance_km(ome_lat, ome_lon, hnd_lat, hnd_lon);
    // 直線距離は約 54.2 km (誤差 ±1.0 km 範囲内を検証)
    assert!(
        (53.0..55.5).contains(&dist),
        "青梅-羽田間の計算距離が不正確です: {:.2} km",
        dist
    );

    // 同一地点の距離は 0.0 km
    let self_dist = haversine_distance_km(ome_lat, ome_lon, ome_lat, ome_lon);
    assert!(self_dist < 0.001);
}

#[test]
fn test_parse_altitude_meters() {
    // 数値の気圧高度 (feet) -> メートル換算
    let val_num = serde_json::json!(10000);
    assert_eq!(parse_altitude_m(&val_num), Some(3048.0));

    // "ground" 文字列の場合は 0m
    let val_ground = serde_json::json!("ground");
    assert_eq!(parse_altitude_m(&val_ground), Some(0.0));

    // null や不正な値
    let val_null = serde_json::Value::Null;
    assert_eq!(parse_altitude_m(&val_null), None);
}

#[test]
fn test_aircraft_json_deserialization() {
    let json_str = r#"
    {
        "now": 1788842309.2,
        "messages": 12345,
        "aircraft": [
            {
                "hex": "86786c",
                "flight": "ANA247  ",
                "alt_baro": 17000,
                "alt_geom": 17200,
                "gs": 420.5,
                "track": 245.0,
                "lat": 35.795,
                "lon": 139.260,
                "seen": 0.2
            },
            {
                "hex": "8412ab",
                "flight": null,
                "alt_baro": "ground",
                "lat": null,
                "lon": null,
                "seen": 15.0
            }
        ]
    }
    "#;

    let parsed: AircraftJson = serde_json::from_str(json_str).expect("aircraft.json のパース成功");
    assert_eq!(parsed.aircraft.len(), 2);

    let ac1 = &parsed.aircraft[0];
    assert_eq!(ac1.hex, "86786c");
    assert_eq!(ac1.clean_callsign().as_deref(), Some("ANA247"));
    assert_eq!(ac1.lat, Some(35.795));
    assert_eq!(ac1.lon, Some(139.260));
    assert_eq!(ac1.altitude_m(), Some(17000.0 * 0.3048));
    assert_eq!(ac1.speed_kmh(), Some(420.5 * 1.852));

    let ac2 = &parsed.aircraft[1];
    assert_eq!(ac2.hex, "8412ab");
    assert_eq!(ac2.clean_callsign(), None);
    assert_eq!(ac2.altitude_m(), Some(0.0));
    assert_eq!(ac2.has_position(), false);
}

#[test]
fn test_adsb_cache_cooldown_and_state() {
    let mut cache = AdsbCache::new(30); // 30分クールダウン

    let hex = "86786c";
    let dist = 5.2;

    // 初回判定: 通知対象 (true)
    assert!(cache.should_notify(hex, dist));
    cache.mark_notified(hex, dist);

    // 2回目 (直後): クールダウン中のため通知対象外 (false)
    assert!(!cache.should_notify(hex, 4.8));

    // 別機体: 通知対象 (true)
    assert!(cache.should_notify("8412ab", 6.0));
}

#[test]
fn test_parse_hexdb_route_json() {
    let json_str = r#"
    {
        "flight": "ANA247",
        "callsign": "ANA247",
        "origin": { "icao": "RJTT", "iata": "HND", "name": "Tokyo Haneda International Airport" },
        "destination": { "icao": "RJFF", "iata": "FUK", "name": "Fukuoka Airport" }
    }
    "#;
    let route = ground_station::adsb::parse_hexdb_route("ANA247", json_str).expect("パース成功");
    assert_eq!(route.callsign, "ANA247");
    assert_eq!(route.origin_iata.as_deref(), Some("HND"));
    assert_eq!(route.destination_iata.as_deref(), Some("FUK"));
}

#[test]
fn test_parse_planespotters_photo_json() {
    let json_str = r#"
    {
        "photos": [
            {
                "id": "12345",
                "thumbnail_large": { "src": "https://cdn.planespotters.net/photo/123.jpg" },
                "link": "https://www.planespotters.net/photo/123",
                "photographer": "John Doe",
                "aircraft_type": "Boeing 787-8 Dreamliner",
                "airline": { "name": "All Nippon Airways" }
            }
        ]
    }
    "#;
    let photo = ground_station::adsb::parse_planespotters_photo(json_str).expect("パース成功");
    assert_eq!(photo.thumbnail_large, "https://cdn.planespotters.net/photo/123.jpg");
    assert_eq!(photo.photographer, "John Doe");
    assert_eq!(photo.aircraft_type.as_deref(), Some("Boeing 787-8 Dreamliner"));
    assert_eq!(photo.airline_name.as_deref(), Some("All Nippon Airways"));
}

