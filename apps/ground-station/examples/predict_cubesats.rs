use anyhow::Result;
use chrono::{FixedOffset, Utc};
use ground_station::config::Config;
use ground_station::orbit::{azimuth_to_direction, OrbitPredictor, SatelliteInfo, SatellitePass, SignalType};
use reqwest::Client;
use std::collections::HashMap;

struct CubeSatDef {
    name_pattern: &'static str,
    display_name: &'static str,
    freq_hz: u64,
    payload: &'static str,
}

#[tokio::main]
async fn main() -> Result<()> {
    let config = Config::load_from_file("config.toml")?;
    let observer = &config.observer;

    let targets = vec![
        CubeSatDef {
            name_pattern: "UMKA 1",
            display_name: "UmKA-1 (RS40S)",
            freq_hz: 437_625_000,
            payload: "📷 望遠鏡カメラ SSTV画像 / GFSK",
        },
        CubeSatDef {
            name_pattern: "SONATE-2",
            display_name: "SONATE-2",
            freq_hz: 437_025_000,
            payload: "📷 AIオンボードカメラ SSDV/SSTV",
        },
        CubeSatDef {
            name_pattern: "FUNCUBE-1",
            display_name: "FUNcube-1 (AO-73)",
            freq_hz: 145_935_000,
            payload: "📊 VHF高感度テレメトリ (BPSK)",
        },
        CubeSatDef {
            name_pattern: "SO-50",
            display_name: "SO-50 (Saudi-OSCAR 50)",
            freq_hz: 436_795_000,
            payload: "📻 常時活発なFMレピータ・テレメトリ",
        },
        CubeSatDef {
            name_pattern: "CAS-4A",
            display_name: "CAS-4A (Zhongwei-1A)",
            freq_hz: 435_220_000,
            payload: "📻 超高SNR 定番CWモールスビーコン",
        },
        CubeSatDef {
            name_pattern: "CUBESAT XI-IV",
            display_name: "XI-IV (東京大学 - 2003年打上)",
            freq_hz: 436_848_000,
            payload: "📻 20年稼働・東大モールスCWビーコン (休眠/微弱)",
        },
        CubeSatDef {
            name_pattern: "ISS (ZARYA)",
            display_name: "ISS (国際宇宙ステーション)",
            freq_hz: 145_800_000,
            payload: "🚀 宇宙飛行士 SSTV画像 / 音声交信",
        },
    ];

    println!("🛰️  CelesTrak から最新アマチュア衛星TLEデータを取得中...");
    let client = Client::builder().user_agent("noaa-station/0.1.0").build()?;
    let url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle";
    let body = client.get(url).send().await?.text().await?;

    let lines: Vec<&str> = body.lines().collect();
    let mut tle_map: HashMap<String, (String, String)> = HashMap::new();

    let mut i = 0;
    while i + 2 < lines.len() {
        let name = lines[i].trim().to_string();
        let l1 = lines[i + 1].trim().to_string();
        let l2 = lines[i + 2].trim().to_string();
        if l1.starts_with('1') && l2.starts_with('2') {
            tle_map.insert(name, (l1, l2));
            i += 3;
        } else {
            i += 1;
        }
    }

    let mut sat_infos = Vec::new();
    let mut payload_map: HashMap<String, &'static str> = HashMap::new();

    for target in &targets {
        for (tle_name, (l1, l2)) in &tle_map {
            if tle_name.contains(target.name_pattern) {
                sat_infos.push(SatelliteInfo {
                    name: target.display_name.to_string(),
                    norad_id: 0,
                    frequency_hz: target.freq_hz,
                    signal_type: SignalType::Lrpt,
                    line1: l1.clone(),
                    line2: l2.clone(),
                });
                payload_map.insert(target.display_name.to_string(), target.payload);
                break;
            }
        }
    }

    let now = Utc::now();
    let duration_hours = 24;
    let min_el = 15.0; // 15度以上

    println!("🔭 観測地点: 東京都青梅市 (北緯 {:.4}°, 東経 {:.4}°, 標高 {:.0}m)", observer.latitude, observer.longitude, observer.altitude_m);
    println!("📅 予測期間: 今後 {} 時間 (最小仰角: {:.0}° 以上)", duration_hours, min_el);
    println!();

    let mut all_passes: Vec<SatellitePass> = Vec::new();
    for sat in &sat_infos {
        if let Ok(passes) = OrbitPredictor::predict_passes_for_satellite(sat, observer, now, duration_hours, min_el) {
            all_passes.extend(passes);
        }
    }

    all_passes.sort_by_key(|p| p.aos);

    if all_passes.is_empty() {
        println!("⚠️  指定期間内に仰角 {:.0}° を超えるパスは見つかりませんでした。", min_el);
        return Ok(());
    }

    let jst = FixedOffset::east_opt(9 * 3600).unwrap();

    println!("==========================================================================================================================");
    println!("{:<20} {:<10} {:<18} {:<8} {:<18} {:<14} {}", "衛星名", "周波数", "AOS (JST)", "継続時間", "最大仰角 (方角)", "ベランダ適性", "主な送信データ");
    println!("--------------------------------------------------------------------------------------------------------------------------");

    for p in all_passes {
        let aos_jst = p.aos.with_timezone(&jst);
        let duration_mins = (p.los - p.aos).num_minutes();
        let duration_secs = (p.los - p.aos).num_seconds() % 60;
        let dir = azimuth_to_direction(p.peak_azimuth_deg);
        let freq_mhz = p.frequency_hz as f64 / 1_000_000.0;
        let payload = payload_map.get(&p.satellite_name).copied().unwrap_or("-");

        // 東向きベランダ (方位角 0°〜180° が東半球。あるいは仰角50°以上の高仰角)
        let is_east_hemisphere = (0.0..=180.0).contains(&p.peak_azimuth_deg);
        let suitability = if p.max_elevation_deg >= 50.0 {
            "🌟 最高 (天頂・高仰角)"
        } else if is_east_hemisphere {
            "✅ 良好 (東側視界)"
        } else {
            "△ 西側 (建物陰)"
        };

        println!(
            "{:<20} {:>7.3}MHz  {}  {:>2}分{:02}秒  {:>4.1}° {:<10} {:<14} {}",
            p.satellite_name,
            freq_mhz,
            aos_jst.format("%m/%d %H:%M:%S"),
            duration_mins,
            duration_secs,
            p.max_elevation_deg,
            dir,
            suitability,
            payload
        );
    }
    println!("==========================================================================================================================");

    Ok(())
}
