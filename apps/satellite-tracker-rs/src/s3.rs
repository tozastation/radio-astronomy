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

        let url = format!(
            "{}/{}/{}",
            self.endpoint_url.trim_end_matches('/'),
            self.bucket_name,
            s3_key.trim_start_matches('/')
        );

        info!(
            "S3Uploader: Uploading {} bytes from {:?} to {}...",
            file_bytes.len(),
            local_path,
            url
        );

        let response = self
            .client
            .put(&url)
            .header("Content-Type", "audio/wav")
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
                error!("S3Uploader: Network error during upload to {}: {}", url, e);
                Ok(false)
            }
        }
    }
}
