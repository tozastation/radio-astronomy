with sats as (
    select * from {{ source('raw', 'ref_satellites') }}
),
pass_counts as (
    select
        canonical_sat_id,
        count(*) filter (where not is_golden) as total_passes,
        count(*) filter (where is_golden) as golden_passes
    from {{ ref('stg_passes') }}
    group by canonical_sat_id
)
select
    s.canonical_id,
    s.satellite_type,
    s.canonical_signal,
    s.expected_artifact,
    s.nominal_freq_mhz,
    coalesce(p.total_passes, 0) as total_passes,
    coalesce(p.golden_passes, 0) as golden_passes
from sats s
left join pass_counts p
    on s.canonical_id = p.canonical_sat_id
