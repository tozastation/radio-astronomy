with anchor as (
    select
        coalesce(max(timestamp_utc), current_timestamp) as ref_ts
    from {{ ref('fct_galactic_scans') }}
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
        g.sky_type,
        g.preempted,
        g.peak_line_snr_db,
        g.continuum_power_db
    from windows w
    cross join anchor a
    join {{ ref('fct_galactic_scans') }} g
        on (
            w.lookback_days is null
            or g.timestamp_utc >= a.ref_ts - (w.lookback_days * interval '1 day')
        )
)
select
    window_label,
    sky_type,
    count(*) as scan_count,
    sum(case when preempted then 1 else 0 end) as preempted_count,
    round(100.0 * sum(case when preempted then 1 else 0 end) / nullif(count(*), 0), 2) as preemption_rate_pct,
    round(avg(peak_line_snr_db), 2) as mean_peak_line_snr_db,
    round(quantile_cont(peak_line_snr_db, 0.95), 2) as p95_peak_line_snr_db,
    round(avg(continuum_power_db), 2) as mean_continuum_power_db
from scoped
group by window_label, sky_type
