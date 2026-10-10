use anyhow::{Context, Result};
use log::{error, info, warn};
use reqwest::Client;
use std::fs;
use std::path::Path;
use std::time::Duration;

/// Garage S3 非同期アップロード ＆ ローカルクリーンアップ
#[derive(Clone)]
pub struct S3Uploader {
    pub endpoint_url: String,
    pub bucket_name: String,
    pub access_key: String,
    pub secret_key: String,
    pub region: String,
    pub mock_mode: bool,
    client: Client,
}

impl S3Uploader {
    /// 新規 S3Uploader を初期化
    pub fn new(
        endpoint_url: String,
        bucket_name: String,
        access_key: String,
        secret_key: String,
    ) -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(60))
            .build()
            .unwrap_or_else(|_| Client::new());

        Self {
            endpoint_url,
            bucket_name,
            access_key,
            secret_key,
            region: "garage".to_string(),
            mock_mode: false,
            client,
        }
    }

    /// 環境変数から S3 設定を読み込んで初期化
    pub fn from_env() -> Self {
        let endpoint_url = std::env::var("GARAGE_S3_ENDPOINT")
            .unwrap_or_else(|_| "http://garage-s3.storage.svc.cluster.local:3900".to_string());
        let bucket_name = std::env::var("GARAGE_BUCKET")
            .unwrap_or_else(|_| "satellite-recordings".to_string());
        let access_key = std::env::var("GARAGE_ACCESS_KEY").unwrap_or_default();
        let secret_key = std::env::var("GARAGE_SECRET_KEY").unwrap_or_default();
        let mock_mode = std::env::var("MOCK_S3").map(|v| v == "true" || v == "1").unwrap_or(false);

        let mut uploader = Self::new(endpoint_url, bucket_name, access_key, secret_key);
        uploader.mock_mode = mock_mode;
        uploader
    }

    /// 指定ファイルを S3 へ PUT アップロードし、成功時にローカルファイルを安全に削除
    pub async fn upload_and_cleanup(&self, local_path: &Path, s3_key: &str) -> Result<bool> {
        if !local_path.exists() {
            warn!("S3Uploader: File not found: {:?}", local_path);
            return Ok(false);
        }

        if self.mock_mode {
            info!(
                "S3Uploader [MOCK]: Uploaded {:?} to s3://{}/{}",
                local_path, self.bucket_name, s3_key
            );
            fs::remove_file(local_path)
                .with_context(|| format!("ローカルモックファイルの削除に失敗: {:?}", local_path))?;
            info!("S3Uploader [MOCK]: Cleaned up local file: {:?}", local_path);
            return Ok(true);
        }

        let file_bytes = fs::read(local_path)
            .with_context(|| format!("WAVファイルの読み込みに失敗: {:?}", local_path))?;

        let url_str = format!(
            "{}/{}/{}",
            self.endpoint_url.trim_end_matches('/'),
            self.bucket_name,
            s3_key.trim_start_matches('/')
        );

        let url_parsed: url::Url = url_str.parse()
            .with_context(|| format!("Invalid S3 URL: {}", url_str))?;

        info!(
            "S3Uploader: Uploading {} bytes from {:?} to {}...",
            file_bytes.len(),
            local_path,
            url_str
        );

        let now = chrono::Utc::now();
        let payload_hash = sha256::digest(&file_bytes);
        let amz_date = now.format("%Y%m%dT%H%M%SZ").to_string();

        let host = url_parsed.host_str().unwrap_or("localhost");
        let host_header = if let Some(port) = url_parsed.port() {
            format!("{}:{}", host, port)
        } else {
            host.to_string()
        };

        let mut headers = http::HeaderMap::new();
        headers.insert("host", host_header.parse().unwrap());
        headers.insert("x-amz-date", amz_date.parse().unwrap());
        headers.insert("x-amz-content-sha256", payload_hash.parse().unwrap());
        headers.insert("content-type", "audio/wav".parse().unwrap());

        let sig = aws_sign_v4::AwsSign::new(
            "PUT",
            &url_str,
            &now,
            &headers,
            &self.region,
            &self.access_key,
            &self.secret_key,
            "s3",
            &file_bytes,
        );
        let auth_header = sig.sign();

        let response = self
            .client
            .put(&url_str)
            .header("Host", &host_header)
            .header("x-amz-date", &amz_date)
            .header("x-amz-content-sha256", &payload_hash)
            .header("Content-Type", "audio/wav")
            .header("Authorization", &auth_header)
            .body(file_bytes)
            .send()
            .await;

        match response {
            Ok(resp) if resp.status().is_success() => {
                info!("S3Uploader: Successfully uploaded: {}", s3_key);

                // アップロード成功後にローカルファイルを安全に削除
                if let Err(e) = fs::remove_file(local_path) {
                    warn!("S3Uploader: Failed to delete local file {:?}: {}", local_path, e);
                } else {
                    info!("S3Uploader: Cleaned up local temporary file: {:?}", local_path);
                }

                Ok(true)
            }
            Ok(resp) => {
                let status = resp.status();
                let err_body = resp.text().await.unwrap_or_default();
                error!(
                    "S3Uploader: Upload failed with status {} for {}: {}",
                    status, s3_key, err_body
                );
                Ok(false)
            }
            Err(e) => {
                error!("S3Uploader: Network error during upload to {}: {}", url_str, e);
                Ok(false)
            }
        }
    }

    /// スプールディレクトリ内の残存未送信 WAV を自動検知して順次 S3 へアップロード
    pub async fn sync_pending_spool(&self, spool_dir: &Path) -> Result<usize> {
        if !spool_dir.exists() {
            return Ok(0);
        }

        let mut uploaded_count = 0;
        let entries = match fs::read_dir(spool_dir) {
            Ok(entries) => entries,
            Err(e) => {
                warn!("S3Uploader: Failed to read spool dir {:?}: {}", spool_dir, e);
                return Ok(0);
            }
        };

        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() && path.extension().and_then(|s| s.to_str()) == Some("wav") {
                let filename = path.file_name().unwrap().to_string_lossy().to_string();
                // ファイル名形式: <Satellite>_<Timestamp>.wav
                let parts: Vec<&str> = filename.split('_').collect();
                let sat_name = if !parts.is_empty() { parts[0] } else { "UNKNOWN" };
                let s3_key = format!("raw/{}/{}", sat_name, filename);

                info!("S3Uploader: Found pending recording {:?}, syncing to S3: {}", path, s3_key);
                match self.upload_and_cleanup(&path, &s3_key).await {
                    Ok(true) => {
                        uploaded_count += 1;
                        info!("S3Uploader: Successfully recovered pending recording: {}", s3_key);
                    }
                    Ok(false) => {
                        warn!("S3Uploader: Failed to upload pending recording: {}", s3_key);
                    }
                    Err(e) => {
                        warn!("S3Uploader: Error uploading pending recording: {}: {}", s3_key, e);
                    }
                }
            }
        }

        Ok(uploaded_count)
    }
}
