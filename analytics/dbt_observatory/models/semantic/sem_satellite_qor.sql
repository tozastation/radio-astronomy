with anchor as (
    select
        coalesce(max(observed_at_utc), current_timestamp) as ref_ts
    from {{ ref('fct_passes') }}
    where not is_golden
),
windows as (
    select '7d' as window_label, 7 as lookback_days
    union all
    select '30d' as window_label, 30 as lookback_days
    union all
    select 'all' as window_label, null as lookback_days
),
scoped as (
    select
        w.window_label,
        p.canonical_sat_id,
        p.satellite_type,
        p.canonical_signal,
        p.outcome,
        p.snr_db,
        p.actual_tca_drift_seconds,
        p.packets_count
    from windows w
    cross join anchor a
    join {{ ref('fct_passes') }} p
        on not p.is_golden
        and (
            w.lookback_days is null
            or p.observed_at_utc >= a.ref_ts - (w.lookback_days * interval '1 day')
        )
)
select
    window_label,
    canonical_sat_id,
    max(satellite_type) as satellite_type,
    max(canonical_signal) as canonical_signal,
    count(*) as total_passes,
    sum(case when outcome = 'success' then 1 else 0 end) as success_count,
    sum(case when outcome = 'failure' then 1 else 0 end) as failure_count,
    sum(case when outcome = 'unknown' then 1 else 0 end) as unknown_count,
    round(
        100.0 * sum(case when outcome = 'success' then 1 else 0 end)
        / nullif(sum(case when outcome in ('success', 'failure') then 1 else 0 end), 0),
        2
    ) as effective_qor_pct,
    round(avg(snr_db), 2) as mean_snr_db,
    count(actual_tca_drift_seconds) as tca_drift_sample_count,
    round(quantile_cont(abs(actual_tca_drift_seconds), 0.95), 2) as p95_abs_tca_drift_sec,
    round(avg(case when outcome = 'success' then packets_count end), 2) as avg_packets_per_success
from scoped
group by window_label, canonical_sat_id
