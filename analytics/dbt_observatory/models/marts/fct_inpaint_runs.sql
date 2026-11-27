with inpaint as (
    select * from {{ ref('stg_inpaint_runs') }}
),
passes as (
    select
        pass_id,
        observed_at_utc,
        canonical_sat_id,
        snr_db,
        is_golden
    from {{ ref('fct_passes') }}
)
select
    i.*,
    p.observed_at_utc,
    p.canonical_sat_id,
    p.snr_db,
    coalesce(p.is_golden, false) as is_golden,
    (coalesce(i.available_channels, '124') != '124') as is_single_band_dropout,
    (coalesce(i.max_gap_px, 0) > coalesce(i.patch_size, 64)) as exceeds_patch_size
from inpaint i
left join passes p
    on i.pass_id = p.pass_id
