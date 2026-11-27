with raw_passes as (
    select * from {{ source('raw', 'raw_passes') }}
),
aliases as (
    select * from {{ source('raw', 'ref_satellite_aliases') }}
),
satellites as (
    select * from {{ source('raw', 'ref_satellites') }}
),
deduped as (
    select
        p.s3_key,
        p.pass_prefix,
        p.pass_id,
        coalesce(a.canonical_id, p.satellite, 'UNKNOWN') as canonical_sat_id,
        p.satellite as raw_satellite_name,
        coalesce(s.satellite_type, p.satellite_type, 'Unknown') as satellite_type,
        coalesce(s.canonical_signal, p.signal_type, 'UNKNOWN') as canonical_signal,
        p.signal_type as raw_signal_type,
        coalesce(s.expected_artifact, 'unknown') as expected_artifact,
        'tokyo-north-balcony' as station_id,
        p.frequency_label,
        p.status,
        coalesce(p.packets_count, 0) as packets_count,
        coalesce(p.has_decoded_image, false) as has_decoded_image,
        coalesce(p.decoded_image_count, 0) as decoded_image_count,
        p.snr_db,
        p.actual_tca_drift_seconds,
        p.duration_seconds,
        p.summary_text,
        p.observed_at_utc,
        p.last_modified_utc,
        coalesce(p.is_golden, false) as is_golden,
        p.synced_at_utc
    from raw_passes p
    left join aliases a
        on p.satellite = a.alias_name
    left join satellites s
        on coalesce(a.canonical_id, p.satellite) = s.canonical_id
    where p.pass_id is not null
    qualify row_number() over (
        partition by p.pass_id
        order by p.last_modified_utc desc nulls last, p.synced_at_utc desc nulls last, p.s3_key desc
    ) = 1
)
select * from deduped
