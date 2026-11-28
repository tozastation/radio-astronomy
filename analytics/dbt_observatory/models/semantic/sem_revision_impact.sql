with target_revs as (
    select
        commit_sha,
        pr_label,
        deployed_at_utc,
        changed_scope,
        affected_satellites,
        summary
    from {{ ref('stg_config_revisions') }}
    where is_tuning_review
      and coalesce(affected_satellites, 'ALL') != ''
    order by deployed_at_utc desc
    limit 5
),
paired as (
    select
        r.commit_sha,
        r.pr_label,
        r.deployed_at_utc,
        r.changed_scope,
        r.summary,
        p.canonical_sat_id,
        case
            when p.observed_at_utc < r.deployed_at_utc then 'before'
            else 'after'
        end as period_side,
        p.outcome,
        p.snr_db
    from target_revs r
    join {{ ref('fct_passes') }} p
        on not p.is_golden
        and p.outcome in ('success', 'failure')
        and p.observed_at_utc >= r.deployed_at_utc - interval '7 day'
        and p.observed_at_utc < r.deployed_at_utc + interval '7 day'
        and (
            coalesce(r.affected_satellites, 'ALL') = 'ALL'
            or list_contains(string_split(r.affected_satellites, ','), p.canonical_sat_id)
        )
)
select
    commit_sha,
    pr_label,
    deployed_at_utc,
    changed_scope,
    summary,
    canonical_sat_id,
    count(*) filter (where period_side = 'before') as sample_count_before,
    count(*) filter (where period_side = 'after') as sample_count_after,
    sum(case when period_side = 'before' and outcome = 'success' then 1 else 0 end) as success_count_before,
    sum(case when period_side = 'after' and outcome = 'success' then 1 else 0 end) as success_count_after,
    round(
        100.0 * sum(case when period_side = 'before' and outcome = 'success' then 1 else 0 end)
        / nullif(count(*) filter (where period_side = 'before'), 0),
        2
    ) as qor_before_pct,
    round(
        100.0 * sum(case when period_side = 'after' and outcome = 'success' then 1 else 0 end)
        / nullif(count(*) filter (where period_side = 'after'), 0),
        2
    ) as qor_after_pct,
    round(
        (
            100.0 * sum(case when period_side = 'after' and outcome = 'success' then 1 else 0 end)
            / nullif(count(*) filter (where period_side = 'after'), 0)
        ) - (
            100.0 * sum(case when period_side = 'before' and outcome = 'success' then 1 else 0 end)
            / nullif(count(*) filter (where period_side = 'before'), 0)
        ),
        2
    ) as qor_delta_pt,
    round(avg(snr_db) filter (where period_side = 'before'), 2) as snr_before_db,
    round(avg(snr_db) filter (where period_side = 'after'), 2) as snr_after_db,
    round(
        avg(snr_db) filter (where period_side = 'after')
        - avg(snr_db) filter (where period_side = 'before'),
        2
    ) as snr_delta_db
from paired
group by commit_sha, pr_label, deployed_at_utc, changed_scope, summary, canonical_sat_id
having count(*) filter (where period_side = 'before') > 0
   and count(*) filter (where period_side = 'after') > 0
