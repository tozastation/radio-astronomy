#[tokio::main]
async fn main() -> anyhow::Result<()> {
    env_logger::init();
    log::info!("Starting satellite-tracker-rs (Ultra-low power edge tracker)...");
    Ok(())
}
