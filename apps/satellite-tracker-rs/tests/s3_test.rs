use satellite_tracker_rs::s3::S3Uploader;
use std::fs;
use std::path::PathBuf;

fn create_temp_test_file(filename: &str, content: &[u8]) -> PathBuf {
    let dir = std::env::temp_dir().join("satellite_s3_test");
    let _ = fs::create_dir_all(&dir);
    let path = dir.join(filename);
    fs::write(&path, content).unwrap();
    path
}

#[tokio::test]
async fn test_s3_upload_and_cleanup_mock_mode() {
    let file_path = create_temp_test_file("test_pass_mock.wav", b"RIFF....dummy_wav");
    assert!(file_path.exists());

    let mut uploader = S3Uploader::new(
        "http://localhost:3900".to_string(),
        "satellite-recordings".to_string(),
        "dummy_key".to_string(),
        "dummy_secret".to_string(),
    );
    uploader.mock_mode = true;

    let success = uploader
        .upload_and_cleanup(&file_path, "raw/ISS/test_pass_mock.wav")
        .await
        .expect("upload call succeeds");

    assert!(success, "アップロードが成功すること");
    assert!(!file_path.exists(), "アップロード成功後にローカルファイルが削除されること");
}

#[tokio::test]
async fn test_s3_upload_file_not_found() {
    let uploader = S3Uploader::new(
        "http://localhost:3900".to_string(),
        "satellite-recordings".to_string(),
        "dummy_key".to_string(),
        "dummy_secret".to_string(),
    );

    let non_existent = PathBuf::from("/tmp/non_existent_file_123456.wav");
    let res = uploader
        .upload_and_cleanup(&non_existent, "raw/ISS/missing.wav")
        .await;

    assert!(res.is_err() || res.unwrap() == false, "存在しないファイルは失敗すること");
}

#[tokio::test]
async fn test_s3_upload_http_put_success() {
    use axum::{routing::put, Router};
    use tokio::net::TcpListener;

    // テスト用の軽量モック HTTP サーバーを起動
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    let app = Router::new().route(
        "/satellite-recordings/*key",
        put(|| async { axum::http::StatusCode::OK }),
    );

    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });

    let file_path = create_temp_test_file("test_pass_http.wav", b"RIFF....http_wav_content");
    assert!(file_path.exists());

    let uploader = S3Uploader::new(
        format!("http://{}", addr),
        "satellite-recordings".to_string(),
        "test_key".to_string(),
        "test_secret".to_string(),
    );

    let success = uploader
        .upload_and_cleanup(&file_path, "raw/ISS/test_pass_http.wav")
        .await
        .expect("HTTP PUT upload succeeds");

    assert!(success, "HTTP PUT アップロードが成功すること");
    assert!(!file_path.exists(), "アップロード成功後にローカルファイルが削除されること");
}
