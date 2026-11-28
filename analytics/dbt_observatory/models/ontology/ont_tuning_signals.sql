with anchor as (
    select
        coalesce(max(observed_at_utc), current_timestamp) as ref_ts
    from {{ ref('ont_pass_diagnosis') }}
    where not is_golden
),
rf_decoder as (
    select
        canonical_sat_id,
        count(*) as cnt,
        round(avg(snr_db), 2) as avg_snr
    from {{ ref('ont_pass_diagnosis') }}
    cross join anchor
    where not is_golden
      and failure_cause = 'HighSNRDecodeFailure'
      and observed_at_utc >= anchor.ref_ts - interval '7 day'
    group by canonical_sat_id
    having count(*) >= {{ var('rf_decoder_min_count') }}
),
tle_drift as (
    select
        canonical_sat_id,
        total_passes,
        tca_drift_sample_count,
        p95_abs_tca_drift_sec
    from {{ ref('sem_satellite_qor') }}
    where window_label = '7d'
      and tca_drift_sample_count >= {{ var('tle_min_samples') }}
      and p95_abs_tca_drift_sec >= {{ var('tca_drift_sec') }}
),
rev_regression as (
    select
        coalesce(pr_label, left(commit_sha, 8)) as rev_ref,
        canonical_sat_id,
        sample_count_before,
        sample_count_after,
        success_count_before,
        success_count_after,
        qor_before_pct,
        qor_after_pct,
        qor_delta_pt
    from {{ ref('sem_revision_impact') }}
    where sample_count_before >= {{ var('min_samples_per_window') }}
      and sample_count_after >= {{ var('min_samples_per_window') }}
      and success_count_before >= {{ var('min_before_success_count') }}
      and qor_delta_pt <= {{ var('qor_drop_pt_threshold') }}
),
ml_shift as (
    select
        config_hash,
        notebook_ref,
        patch_size,
        run_count,
        single_band_dropout_rate_pct,
        large_gap_rate_pct
    from {{ ref('sem_inpaint_quality') }}
    where large_gap_rate_pct >= {{ var('large_gap_rate_pct_threshold') }}
       or single_band_dropout_rate_pct >= {{ var('single_band_dropout_rate_pct_threshold') }}
),
gal_agg as (
    select
        window_label,
        sum(scan_count) as total_scans,
        round(sum(scan_count * mean_continuum_power_db) / nullif(sum(scan_count), 0), 2) as weighted_cont_db
    from {{ ref('sem_galactic_snr') }}
    where window_label in ('7d', '30d')
      and mean_continuum_power_db is not null
    group by window_label
),
gal_spike as (
    select
        w7.total_scans as scans_7d,
        w7.weighted_cont_db as cont_7d_db,
        w30.weighted_cont_db as cont_30d_db,
        round(w7.weighted_cont_db - w30.weighted_cont_db, 2) as delta_db
    from gal_agg w7
    join gal_agg w30 on w30.window_label = '30d'
    where w7.window_label = '7d'
      and w7.total_scans >= {{ var('galactic_min_scans') }}
      and (w7.weighted_cont_db - w30.weighted_cont_db) >= {{ var('continuum_delta_db_threshold') }}
)
select
    'RF_DECODER_TUNING' as signal_id,
    'high' as severity,
    canonical_sat_id as target_entity,
    '直近7日で高SNR (平均 ' || cast(avg_snr as varchar) || ' dB) にもかかわらず復号失敗が ' || cast(cnt as varchar) || ' 件発生' as evidence_summary,
    'apps/satellite-analyzer のデコーダ設定 (ゲイン、スケルチ、サンプリング周波数オフセット等) を点検する' as recommended_action
from rf_decoder

union all

select
    'TLE_OR_CLOCK_DRIFT' as signal_id,
    'medium' as severity,
    canonical_sat_id as target_entity,
    '直近7日間の TCA ドリフト p95 が ' || cast(p95_abs_tca_drift_sec as varchar) || ' 秒 (有効計測 n=' || cast(tca_drift_sample_count as varchar) || '/' || cast(total_passes as varchar) || ')' as evidence_summary,
    'satellite-tracker-rs の TLE 更新周期およびホスト時刻同期 (chrony/systemd-timesyncd) を確認する' as recommended_action
from tle_drift

union all

select
    'REVISION_REGRESSION' as signal_id,
    'high' as severity,
    canonical_sat_id || ' (' || rev_ref || ')' as target_entity,
    'PR適用前後で QoR が ' || cast(qor_before_pct as varchar) || '% -> ' || cast(qor_after_pct as varchar) || '% (' || cast(qor_delta_pt as varchar) || ' pt, n=' || cast(sample_count_before as varchar) || '/' || cast(sample_count_after as varchar) || ')' as evidence_summary,
    '該当 PR の変更内容をレビューし、閾値・パラメータのロールバックまたは再調整を検討する' as recommended_action
from rev_regression

union all

select
    'ML_MASK_DISTRIBUTION_SHIFT' as signal_id,
    'medium' as severity,
    notebook_ref || ' (' || config_hash || ')' as target_entity,
    '実測 ' || cast(run_count as varchar) || ' 件で patch_size(' || cast(patch_size as varchar) || 'px)超過率 ' || cast(large_gap_rate_pct as varchar) || '%, 単一Ch欠落率 ' || cast(single_band_dropout_rate_pct as varchar) || '%' as evidence_summary,
    'ml-astronomy (projects/radio-ml) の次期学習でパッチサイズ拡大やバンドドロップ拡張比率の見直しを検討する' as recommended_action
from ml_shift

union all

select
    'GALACTIC_NOISE_FLOOR_SPIKE' as signal_id,
    'medium' as severity,
    'galactic-21cm' as target_entity,
    '直近7日の連続波パワー平均 ' || cast(cont_7d_db as varchar) || ' dB (30日平均 ' || cast(cont_30d_db as varchar) || ' dB, +' || cast(delta_db as varchar) || ' dB, n=' || cast(scans_7d as varchar) || ')' as evidence_summary,
    '1420 MHz 帯周辺のローカル RFI 発生源や SDR ゲイン設定の変動を確認する' as recommended_action
from gal_spike
