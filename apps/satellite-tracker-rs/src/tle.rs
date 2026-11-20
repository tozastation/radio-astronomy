use crate::orbit::{merge_tle_catalog, parse_tle_epoch};
use crate::s3::S3Uploader;
use log::{info, warn};
use reqwest::Client;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

/// 対象衛星の初期定義 (外部通信・キャッシュ不在時の最終フォールバック)
pub struct TargetSatellite {
    pub name: &'static str,
    pub norad_id: u32,
    pub line1: &'static str,
    pub line2: &'static str,
    pub freq_hz: f64,
}

#[derive(Clone, Debug)]
pub struct RuntimeTarget {
    pub name: &'static str,
    pub norad_id: u32,
    pub line1: String,
    pub line2: String,
    pub freq_hz: f64,
}

pub const DEFAULT_TARGETS: &[TargetSatellite] = &[
    TargetSatellite {
        name: "ISS (ZARYA)",
        norad_id: 25544,
        line1: "1 25544U 98067A   26319.91560506  .00009214  00000+0  17269-3 0  9996",
        line2: "2 25544  51.6321 269.2481 0007362  30.2971 329.8442 15.49402183595279",
        freq_hz: 145_825_000.0,
    },
    TargetSatellite {
        name: "METEOR-M2 3",
        norad_id: 57166,
        line1: "1 57166U 23091A   26320.21578751 -.00000010  00000+0  14340-4 0  9996",
        line2: "2 57166  98.5893  11.9287 0005071  89.8347 270.3413 14.24056598176153",
        freq_hz: 137_900_000.0,
    },
    TargetSatellite {
        name: "METEOR-M2 4",
        norad_id: 59051,
        line1: "1 59051U 24039A   26320.19173317  .00000028  00000+0  32366-4 0  9996",
        line2: "2 59051  98.7248 277.7315 0008145  89.7937 270.4174 14.22443462140870",
        freq_hz: 137_900_000.0,
    },
    TargetSatellite {
        name: "FUNCUBE-1 (AO-73)",
        norad_id: 39444,
        line1: "1 39444U 13066AE  26319.89930512  .00008247  00000+0  48531-3 0  9992",
        line2: "2 39444  97.8476 294.8869 0036272  48.8187 311.6161 15.11350389702564",
        freq_hz: 145_935_000.0,
    },
];

/// ローカル TLE 候補パス一覧を返す
pub fn candidate_tle_paths(spool_dir: &Path) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(env_path) = std::env::var("TLE_FILE_PATH") {
        if !env_path.trim().is_empty() {
            candidates.push(PathBuf::from(env_path));
        }
    }
    candidates.push(spool_dir.join("tles.txt"));
    candidates.push(PathBuf::from("/etc/satellite-tracker/tles.txt"));
    candidates.push(PathBuf::from("apps/ground-station/data/cache/tles.txt"));
    candidates.push(PathBuf::from("data/cache/tles.txt"));
    candidates
}

/// 複数のローカル TLE キャッシュおよび DEFAULT_TARGETS から、NORAD ID ごとに最新 Epoch の TLE を選択
pub fn load_runtime_targets(spool_dir: &Path) -> Vec<RuntimeTarget> {
    let mut catalog: HashMap<u32, (String, String, String)> = HashMap::new();

    for t in DEFAULT_TARGETS {
        catalog.insert(
            t.norad_id,
            (t.name.to_string(), t.line1.to_string(), t.line2.to_string()),
        );
    }

    for path in candidate_tle_paths(spool_dir) {
        if path.is_file() {
            if let Ok(text) = std::fs::read_to_string(&path) {
                merge_tle_catalog(&text, &mut catalog);
            }
        }
    }

    build_targets_from_catalog(&catalog)
}

fn build_targets_from_catalog(
    catalog: &HashMap<u32, (String, String, String)>,
) -> Vec<RuntimeTarget> {
    DEFAULT_TARGETS
        .iter()
        .map(|t| {
            if let Some((_, l1, l2)) = catalog.get(&t.norad_id) {
                RuntimeTarget {
                    name: t.name,
                    norad_id: t.norad_id,
                    line1: l1.clone(),
                    line2: l2.clone(),
                    freq_hz: t.freq_hz,
                }
            } else {
                RuntimeTarget {
                    name: t.name,
                    norad_id: t.norad_id,
                    line1: t.line1.to_string(),
                    line2: t.line2.to_string(),
                    freq_hz: t.freq_hz,
                }
            }
        })
        .collect()
}

/// キャッシュファイルが TTL (秒) 以内かつ対象衛星の Epoch が有効かを判定
pub fn is_tle_cache_fresh(cache_path: &Path, ttl_secs: u64) -> bool {
    if let Ok(meta) = std::fs::metadata(cache_path) {
        if let Ok(modified) = meta.modified() {
            if let Ok(elapsed) = SystemTime::now().duration_since(modified) {
                return elapsed < Duration::from_secs(ttl_secs);
            }
        }
    }
    false
}

/// CelesTrak / ミラー API から最新 TLE を取得し、SPOOL_DIR/tles.txt および S3 (tle/tles.txt) へ保存
pub async fn refresh_tle_cache_if_needed(
    spool_dir: &Path,
    ttl_secs: u64,
    uploader: Option<&S3Uploader>,
) -> Option<Vec<RuntimeTarget>> {
    let effective_ttl = ttl_secs.max(7200); // CelesTrak AUP (最低2時間以上) 遵守
    let cache_path = spool_dir.join("tles.txt");
    if is_tle_cache_fresh(&cache_path, effective_ttl) {
        return None;
    }

    let client = Client::builder()
        .timeout(Duration::from_secs(5))
        .user_agent("radio-astronomy-satellite-tracker/1.8")
        .build()
        .ok()?;

    let mut catalog: HashMap<u32, (String, String, String)> = HashMap::new();
    for t in DEFAULT_TARGETS {
        catalog.insert(
            t.norad_id,
            (t.name.to_string(), t.line1.to_string(), t.line2.to_string()),
        );
    }
    if cache_path.is_file() {
        if let Ok(existing) = std::fs::read_to_string(&cache_path) {
            merge_tle_catalog(&existing, &mut catalog);
        }
    }

    let mut updated_from_net = false;
    let urls = [
        "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle",
        "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle",
    ];

    let mut fetched_catalog: HashMap<u32, (String, String, String)> = HashMap::new();
    for url in urls {
        match client.get(url).send().await {
            Ok(resp) if resp.status().is_success() => {
                if let Ok(text) = resp.text().await {
                    merge_tle_catalog(&text, &mut fetched_catalog);
                    updated_from_net = true;
                }
            }
            Ok(resp) => {
                warn!("TLE fetch from {} returned HTTP {}", url, resp.status());
            }
            Err(e) => {
                warn!("TLE fetch from {} failed: {}", url, e);
            }
        }
    }

    // CelesTrak で取得できなかった衛星があればミラー API から補完
    for t in DEFAULT_TARGETS {
        if !fetched_catalog.contains_key(&t.norad_id) {
            let mirror_url = format!("https://tle.ivanstanojevic.me/api/tle/{}", t.norad_id);
            if let Ok(resp) = client.get(&mirror_url).send().await {
                if resp.status().is_success() {
                    if let Ok(val) = resp.json::<serde_json::Value>().await {
                        if let (Some(name), Some(l1), Some(l2)) = (
                            val.get("name").and_then(|v| v.as_str()),
                            val.get("line1").and_then(|v| v.as_str()),
                            val.get("line2").and_then(|v| v.as_str()),
                        ) {
                            let block = format!("{}\n{}\n{}", name, l1, l2);
                            merge_tle_catalog(&block, &mut fetched_catalog);
                            updated_from_net = true;
                        }
                    }
                }
            }
        }
    }

    if !updated_from_net {
        return None;
    }

    for (norad_id, entry) in fetched_catalog {
        let new_epoch = parse_tle_epoch(&entry.1);
        let should_replace = match catalog.get(&norad_id) {
            Some((_, old_l1, _)) => new_epoch > parse_tle_epoch(old_l1),
            None => true,
        };
        if should_replace {
            catalog.insert(norad_id, entry);
        }
    }

    let targets = build_targets_from_catalog(&catalog);
    let mut serialized = String::new();
    for t in &targets {
        serialized.push_str(&format!("{}\n{}\n{}\n", t.name, t.line1, t.line2));
    }

    if let Err(e) = std::fs::create_dir_all(spool_dir) {
        warn!("Failed to create spool_dir {:?} for TLE cache: {}", spool_dir, e);
    } else if let Err(e) = std::fs::write(&cache_path, &serialized) {
        warn!("Failed to write TLE cache {:?}: {}", cache_path, e);
    } else {
        info!("Updated live TLE cache at {:?}", cache_path);
        if let Some(up) = uploader {
            let _ = up.upload_file_keep_local(&cache_path, "tle/tles.txt").await;
        }
    }

    Some(targets)
}
