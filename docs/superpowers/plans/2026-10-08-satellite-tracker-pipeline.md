# UHF帯CubeSat電波観測パイプライン 実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 北向きベランダの15cm固定長アンテナと RTL-SDR Blog V4 を用い、UHF帯（435〜438MHz）CubeSatの自動追尾・エッジDSP（ドップラーS字解析 & RSSI）を実行する KubeEdge 観測ワークロードおよび Grafana 可視化パイプラインを構築する。

**Architecture:** 軌道力学（SGP4）による北天パス予測、エッジDSP（FFTパワースペクトル & 実測ドップラー抽出）、Prometheus Exporter（:9100）をまとめた軽量Pythonコンテナを開発する。クラスタ内ローカルレジストリと nerdctl を通じて containerd へデプロイし、kube-prometheus-stack と Grafana ダッシュボードで電波強度とドップラー偏移をリアルタイム可視化する。

**Tech Stack:** Python 3.11+, Skyfield, SGP4, pyrtlsdr, NumPy, SciPy, Prometheus Client, nerdctl, containerd, k3s, KubeEdge, Grafana

**Spec:** `docs/superpowers/specs/2026-10-08-satellite-tracker-design.md`

## Global Constraints

- アンテナ長 $15\,\text{cm}$（基本共振周波数 $f \approx 475\,\text{MHz}$、UHF帯 $430 \sim 480\,\text{MHz}$ 最適整合）。
- 観測地制約: 北向きベランダ（マンション5階、Azimuth $270^\circ \sim 90^\circ$、最低仰角 Elevation $\ge 10^\circ$）。
- コンテナランタイム: Docker daemon 依存を排除し、k3s 内蔵の CRI `containerd`（`/run/k3s/containerd/containerd.sock`）に統一。
- コミットルール: 日本語で記述、絵文字禁止、Conventional Commits（`feat:`, `docs:`, `fix:`）。
- 実機非依存性: `MOCK_SDR=true` モードをサポートし、SDR ハードウェア未接続環境でもテスト・可視化を 100% 完結可能にする。

## Review Focus

1. **オフライン・TLE 取得失敗**: ネットワーク断時に Celestrak 取得が失敗しても、バンドルされた静的 TLE でフォールバック動作すること。
2. **北向きベランダ視界判定の境界値**: 方位角（Azimuth）が $270^\circ$（真西）および $90^\circ$（真東）を跨ぐ際の境界判定が正しく機能すること。
3. **実機 SDR ビジー / 未接続**: RTL-SDR が接続されていない場合にコンテナがクラッシュループせず、ログを出力してモックまたは待機へ安全に遷移すること。
4. **ドップラーS字カーブのゼロクロス**: TCA（最接近点）において実測および予測ドップラー偏移が $0\,\text{Hz}$ 付近で交差すること。
5. **Prometheus メトリクスの欠損防止**: 衛星待機中（非通過中）でも `satellite_tracking_active=0` および次回 AOS タイムスタンプが正常に出力されること。

---

### Task 1: KubeEdge 基本デプロイガイドの作成

**Files:**
- Create: `docs/setup/03_kubeedge_deployment_guide.md`

**Interfaces:**
- Produces: KubeEdge CloudCore / EdgeCore の Ubuntu 26.04 上での手動デプロイ手順書（一次情報リンク、設定手順、トラブルシューティング付き）

- [ ] **Step 1: ドキュメントのドラフト作成**
  KubeEdge 公式ドキュメント、keadm リポジトリへのリンク、udev ルール（RTL-SDR アクセス権）、k3s containerd 設定（`registries.yaml`）、`keadm init` / `keadm join` コマンド、Edged 10350 開通確認手順を記載する。
- [ ] **Step 2: 一次情報リンクとコマンドの整合性確認**
  ドキュメント内の URL や systemd コマンド、CRI ソケットパス（`/run/k3s/containerd/containerd.sock`）を検証する。
- [ ] **Step 3: コミット**
  ```bash
  git add docs/setup/03_kubeedge_deployment_guide.md
  git commit -m "docs: KubeEdge基本コンポーネントのデプロイガイドを追加"
  ```

---

### Task 2: インフラ基盤（クラスタ内レジストリ）マニフェストの作成

**Files:**
- Create: `infrastructure/registry/deployment.yaml`
- Create: `infrastructure/registry/service.yaml`
- Create: `infrastructure/registry/registries.yaml`

**Interfaces:**
- Produces: `localhost:5000` で待ち受ける OCI レジストリ（`registry:2`）と k3s containerd ミラー設定

- [ ] **Step 1: レジストリマニフェストを作成**
  `registry:2` をホストストレージ（`/var/lib/registry`）マウントで動かす Deployment と、`hostPort: 5000`（または NodePort）で公開する Service を作成する。
- [ ] **Step 2: k3s containerd 用の `registries.yaml` 設定サンプルを作成**
  `localhost:5000` および `127.0.0.1:5000` を HTTP（平文）通信許可する設定を定義する。
- [ ] **Step 3: YAML 構文検証**
  `python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ['infrastructure/registry/deployment.yaml', 'infrastructure/registry/service.yaml']]"` を実行してシンタックスを確認する。
- [ ] **Step 4: コミット**
  ```bash
  git add infrastructure/registry/
  git commit -m "feat: クラスタ内ローカルレジストリのマニフェストを追加"
  ```

---

### Task 3: 衛星観測ワークロード（Orbit Predictor: SGP4軌道計算・北天フィルタ）の実装とテスト

**Files:**
- Create: `apps/satellite-tracker/requirements.txt`
- Create: `apps/satellite-tracker/src/__init__.py`
- Create: `apps/satellite-tracker/src/orbit_predictor.py`
- Create: `apps/satellite-tracker/tests/__init__.py`
- Create: `apps/satellite-tracker/tests/test_orbit_predictor.py`

**Interfaces:**
- Produces: `OrbitPredictor` クラス
  - `__init__(observer_lat: float, observer_lon: float, observer_elev_m: float, balcony_facing: str = "NORTH")`
  - `get_next_pass(satellite_name: str, tle_line1: str, tle_line2: str, start_time: datetime) -> Optional[PassInfo]`
  - `calculate_current_position(satellite_name: str, tle_line1: str, tle_line2: str, current_time: datetime) -> SatellitePosition`
  - `PassInfo`: `dataclass(satellite_name, aos_time, tca_time, los_time, max_elevation, frequency_hz)`
  - `SatellitePosition`: `dataclass(elevation, azimuth, range_km, radial_velocity_km_s, doppler_shift_hz)`

- [ ] **Step 1: 依存関係定義（requirements.txt）を作成**
  `skyfield>=1.48`, `sgp4>=2.23`, `numpy>=1.26`, `prometheus-client>=0.20`, `pytest>=8.0` を記載する。
- [ ] **Step 2: 失敗するユニットテストを作成（test_orbit_predictor.py）**
  ISS（437.550MHz）または代表的衛星の TLE を用い、北向きベランダ視界内（Azimuth $270^\circ \sim 90^\circ$ かつ Elevation $\ge 10^\circ$）における AOS/TCA/LOS の計算、および TCA 時のドップラー偏移が約 $0\,\text{Hz}$、接近時が正、離脱時が負になることを検証するテストを書く。
- [ ] **Step 3: テスト実行（失敗を確認）**
  `pytest apps/satellite-tracker/tests/test_orbit_predictor.py -v`（未実装エラーを確認）
- [ ] **Step 4: `OrbitPredictor` を実装（src/orbit_predictor.py）**
  Skyfield / SGP4 を使用して軌道計算、視線速度（Radial velocity）からのドップラー計算、および北向きベランダ方位角フィルタを実装する。TLE 取得失敗時の静的キャッシュフォールバックを組み込む。
- [ ] **Step 5: テスト実行（成功を確認）**
  `pytest apps/satellite-tracker/tests/test_orbit_predictor.py -v`（PASS を確認）
- [ ] **Step 6: コミット**
  ```bash
  git add apps/satellite-tracker/
  git commit -m "feat: SGP4衛星軌道計算および北天視界フィルタを実装"
  ```

---

### Task 4: 衛星観測ワークロード（SDR DSP コレクター: FFT / RSSI / ドップラー抽出）の実装とテスト

**Files:**
- Create: `apps/satellite-tracker/src/sdr_collector.py`
- Create: `apps/satellite-tracker/tests/test_sdr_collector.py`

**Interfaces:**
- Consumes: `OrbitPredictor` からの対象周波数・ドップラー予測値
- Produces: `SDRCollector` クラス
  - `__init__(mock_sdr: bool = False, sample_rate: float = 2.4e6, fft_size: int = 2048)`
  - `start()`
  - `stop()`
  - `measure_spectrum(center_freq_hz: float, expected_doppler_hz: float = 0.0) -> SpectrumResult`
  - `SpectrumResult`: `dataclass(center_freq_hz, peak_freq_hz, measured_doppler_hz, rssi_dbm, snr_db)`

- [ ] **Step 1: 失敗するテストを作成（test_sdr_collector.py）**
  `mock_sdr=True` モードにおいて、既知の周波数オフセット（例: $+5\,\text{kHz}$）と SNR を持つテスト IQ 信号を生成させ、FFT パワースペクトルからピーク周波数を正しく検出し、`rssi_dbm` と `measured_doppler_hz` を抽出できることを検証するテストを書く。
- [ ] **Step 2: テスト実行（失敗を確認）**
  `pytest apps/satellite-tracker/tests/test_sdr_collector.py -v`
- [ ] **Step 3: `SDRCollector` を実装（src/sdr_collector.py）**
  - ハニング窓 + NumPy FFT によるパワースペクトル計算。
  - チャネル内ピーク探索と放物線補間（二次補間）によるサブビン周波数推定。
  - ノイズフロア推定に基づく SNR 算出。
  - `mock_sdr=True` 時の合成信号ジェネレータ（信号＋ガウス雑音）。
  - 実機 RTL-SDR 利用時の初期化および例外処理（デバイス未検出時の安全フォールバック）。
- [ ] **Step 4: テスト実行（成功を確認）**
  `pytest apps/satellite-tracker/tests/test_sdr_collector.py -v`（PASS を確認）
- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker/src/sdr_collector.py apps/satellite-tracker/tests/test_sdr_collector.py
  git commit -m "feat: SDR FFTスペクトル解析およびRSSI・ドップラー抽出処理を実装"
  ```

---

### Task 5: 観測メトリクス Exporter とメインループの実装・結合テスト

**Files:**
- Create: `apps/satellite-tracker/src/metrics_exporter.py`
- Create: `apps/satellite-tracker/src/main.py`
- Create: `apps/satellite-tracker/tests/test_metrics_exporter.py`

**Interfaces:**
- Consumes: `OrbitPredictor`, `SDRCollector`
- Produces: Prometheus HTTP エンドポイント（`:9100/metrics`）および常駐スケジューラデーモン

- [ ] **Step 1: 失敗するテストを作成（test_metrics_exporter.py）**
  `MetricsExporter` を起動し、`satellite_tracking_active`, `satellite_rssi_dbm`, `satellite_doppler_measured_hz`, `satellite_next_aos_timestamp_seconds` 等が Prometheus 形式テキストとして正しく出力されることを検証するテストを書く。
- [ ] **Step 2: テスト実行（失敗を確認）**
  `pytest apps/satellite-tracker/tests/test_metrics_exporter.py -v`
- [ ] **Step 3: `MetricsExporter` と `main.py` を実装**
  - `metrics_exporter.py`: Prometheus クライアントを用いた Gauge / Counter 定義および更新メソッド。
  - `main.py`: 環境変数読み込み（緯度経度、北向き設定、MOCK_SDR等）、定期的な軌道計算ループ、パス突入時の SDR 観測起動、メトリクス更新ループ。
- [ ] **Step 4: テスト実行（成功を確認）**
  `pytest apps/satellite-tracker/tests/test_metrics_exporter.py -v`（PASS を確認）
- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker/src/metrics_exporter.py apps/satellite-tracker/src/main.py apps/satellite-tracker/tests/test_metrics_exporter.py
  git commit -m "feat: 観測メトリクスExporterとメインスケジューラループを実装"
  ```

---

### Task 6: Dockerfile、Kubernetes マニフェスト（Deployment, Service, ServiceMonitor）の作成

**Files:**
- Create: `apps/satellite-tracker/Dockerfile`
- Create: `infrastructure/apps/satellite-tracker/deployment.yaml`
- Create: `infrastructure/apps/satellite-tracker/service.yaml`
- Create: `infrastructure/apps/satellite-tracker/servicemonitor.yaml`

**Interfaces:**
- Produces: OCI イメージ定義および KubeEdge デプロイ用マニフェスト

- [ ] **Step 1: Dockerfile を作成**
  マルチステージまたは軽量 `python:3.11-slim` ベースで、`librtlsdr-dev`, `librtlsdr0` をインストールし、アプリケーションを配置する Dockerfile を作成する。
- [ ] **Step 2: Kubernetes マニフェストを作成**
  - `deployment.yaml`: `nodeSelector: node-role.kubernetes.io/edge: ""`、`/dev/bus/usb` ホストパスマウント、`privileged: true`、環境変数設定。
  - `service.yaml`: ポート 9100（`name: metrics`）。
  - `servicemonitor.yaml`: `kube-prometheus-stack` スクレイプ用定義。
- [ ] **Step 3: YAML 検証**
  `python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ['infrastructure/apps/satellite-tracker/deployment.yaml', 'infrastructure/apps/satellite-tracker/service.yaml', 'infrastructure/apps/satellite-tracker/servicemonitor.yaml']]"` を実行してシンタックスを確認する。
- [ ] **Step 4: コミット**
  ```bash
  git add apps/satellite-tracker/Dockerfile infrastructure/apps/satellite-tracker/
  git commit -m "feat: 衛星観測PodのDockerfileおよびKubernetesマニフェストを追加"
  ```

---

### Task 7: Grafana ダッシュボード定義（JSON）の作成

**Files:**
- Create: `infrastructure/monitoring/dashboards/satellite-tracker.json`

**Interfaces:**
- Produces: Grafana ダッシュボード JSON（ステータスバナー、リアルタイム RSSI/SNR、ドップラーS字解析、軌道推移、エッジリソース負荷）

- [ ] **Step 1: ダッシュボード JSON を作成**
  Spec 4.5 に定義した 5 つのパネル構成（PromQL クエリを含む）を記述した JSON を作成する。
  - `satellite_tracking_active`
  - `satellite_rssi_dbm`
  - `satellite_doppler_predicted_hz` vs `satellite_doppler_measured_hz`（S字カーブ）
  - `satellite_elevation_degrees` & `satellite_azimuth_degrees`
  - `container_cpu_usage_seconds_total` / `container_memory_working_set_bytes`（Edged 10350 から取得）
- [ ] **Step 2: JSON 構文検証**
  `python3 -m json.tool infrastructure/monitoring/dashboards/satellite-tracker.json > /dev/null` でパースを確認する。
- [ ] **Step 3: コミット**
  ```bash
  git add infrastructure/monitoring/dashboards/satellite-tracker.json
  git commit -m "feat: 衛星追尾およびドップラー観測用Grafanaダッシュボードを追加"
  ```

---

### Task 8: ナレッジ記録（docs/04_qa.md の更新）と全体検証

**Files:**
- Modify: `docs/04_qa.md`

**Interfaces:**
- Produces: 15cm アンテナの共振物理、北向きベランダと極軌道衛星、CRI containerd とローカルレジストリ選定理由を体系的に記録した Q&A ドキュメント

- [ ] **Step 1: docs/04_qa.md に新しい質疑応答を追記**
  数式（$\lambda=c/f$）、記号一覧、物理的読み解き、展開ステップの3点セットを用いて、今回の設計決定事項を Q&A としてまとめる。
- [ ] **Step 2: 全テストの実行**
  `pytest apps/satellite-tracker/tests/ -v` を実行し、全テストが通過することを確認する。
- [ ] **Step 3: コミット**
  ```bash
  git add docs/04_qa.md
  git commit -m "docs: 15cmアンテナ共振とCubeSat観測設計に関するQ&Aを追加"
  ```
