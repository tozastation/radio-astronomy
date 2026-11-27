with passes as (
    select * from {{ ref('stg_passes') }}
),
revs as (
    select * from {{ ref('stg_config_revisions') }}
),
joined as (
    select
        p.*,
        r.commit_sha as config_commit_sha,
        r.pr_label as config_pr_label,
        case
            when lower(coalesce(p.raw_signal_type, p.canonical_signal, '')) like '%fm%'
                 or lower(coalesce(p.raw_signal_type, '')) like '%voice%'
                 or p.expected_artifact = 'audio_only'
                then 'unknown'
            when p.has_decoded_image or p.packets_count > 0
                then 'success'
            else 'failure'
        end as outcome
    from passes p
    left join revs r
        on r.deployed_at_utc <= p.observed_at_utc
    qualify row_number() over (
        partition by p.pass_id
        order by r.deployed_at_utc desc nulls last
    ) = 1
)
select * from joined
