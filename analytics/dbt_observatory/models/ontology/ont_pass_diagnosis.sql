with passes as (
    select * from {{ ref('fct_passes') }}
),
inpaint as (
    select * from {{ ref('fct_inpaint_runs') }}
)
select
    p.pass_id,
    p.canonical_sat_id,
    p.satellite_type,
    p.canonical_signal,
    p.observed_at_utc,
    p.outcome,
    p.snr_db,
    p.actual_tca_drift_seconds,
    p.is_golden,
    p.config_commit_sha,
    p.config_pr_label,
    i.config_hash as inpaint_config_hash,
    i.tier as inpaint_tier,
    i.seam_jump_l1 as inpaint_seam_jump_l1,
    i.mean_uncertainty as inpaint_mean_uncertainty,
    case
        when p.is_golden then 'GoldenBenchmark'
        when p.outcome = 'unknown' then 'UnverifiableSignal'
        when p.outcome = 'failure' and p.snr_db is not null and p.snr_db >= {{ var('high_snr_db') }}
            then 'HighSNRDecodeFailure'
        when p.outcome = 'failure' and p.actual_tca_drift_seconds is not null and abs(p.actual_tca_drift_seconds) >= {{ var('tca_drift_sec') }}
            then 'TcaDriftAnomaly'
        when p.outcome = 'failure'
            then 'LowSNRWeakSignal'
        when p.outcome = 'success' and i.pass_id is not null and (
            i.tier = 'Tier C'
            or (i.seam_jump_l1 is not null and i.seam_jump_l1 >= {{ var('seam_jump_l1_degraded') }})
        )
            then 'InpaintDegraded'
        else 'Healthy'
    end as failure_cause,
    case
        when i.pass_id is not null and coalesce(i.missing_pct, 0) > 0 then 'ModelEstimated'
        when i.pass_id is not null and coalesce(i.partial_valid_pct, 0) > 0 then 'PartiallyRecovered'
        else 'Observed'
    end as epistemic_status,
    case
        when p.is_golden then false
        when p.outcome = 'failure' and (
            (p.snr_db is not null and p.snr_db >= {{ var('high_snr_db') }})
            or (p.actual_tca_drift_seconds is not null and abs(p.actual_tca_drift_seconds) >= {{ var('tca_drift_sec') }})
        ) then true
        else false
    end as is_tunable,
    case
        when not p.is_golden and p.outcome = 'success' and i.pass_id is not null and (
            i.tier = 'Tier C'
            or (i.seam_jump_l1 is not null and i.seam_jump_l1 >= {{ var('seam_jump_l1_degraded') }})
        ) then true
        else false
    end as is_ml_retrain_signal
from passes p
left join inpaint i
    on p.pass_id = i.pass_id
