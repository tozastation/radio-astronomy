select
    commit_sha,
    pr_label,
    deployed_at_utc,
    changed_scope,
    summary,
    (
        lower(coalesce(pr_label, '')) like '%tuning-review%'
        or lower(coalesce(summary, '')) like '%tuning%'
        or changed_scope in ('tracker', 'analyzer')
    ) as is_tuning_review
from {{ source('raw', 'raw_config_revisions') }}
where commit_sha is not null
