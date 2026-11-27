select
    *,
    cast(timestamp_utc as date) as scan_date_utc
from {{ ref('stg_galactic_scans') }}
