with raw_inpaint as (
    select * from {{ source('raw', 'raw_inpaint_runs') }}
),
model_versions as (
    select * from {{ source('raw', 'ref_model_versions') }}
),
deduped as (
    select
        r.s3_key,
        r.pass_id,
        r.satellite as raw_satellite_name,
        r.schema_version,
        r.generated_at_utc,
        r.device,
        coalesce(mv.model_name, r.model_name) as model_name,
        coalesce(mv.model_mode, r.model_mode) as model_mode,
        r.quality_preset,
        r.bundle_file,
        r.config_hash,
        r.base_bundle_hash,
        mv.notebook_ref,
        mv.input_channels,
        mv.patch_size,
        mv.gate_channels,
        mv.loss_profile,
        mv.extreme_mask_ratio,
        mv.val_psnr_db,
        mv.val_sobel_pct,
        r.image_height,
        r.image_width,
        r.available_channels,
        r.all_valid_pct,
        r.partial_valid_pct,
        r.missing_pct,
        r.any_valid_pct,
        r.max_gap_px,
        r.reg_mode,
        r.clear_agree_pct,
        coalesce(r.used_prev_atlas, false) as used_prev_atlas,
        coalesce(r.prev_source_passes_count, 0) as prev_source_passes_count,
        r.tier,
        r.tier_label,
        r.seam_jump_l1,
        r.mean_uncertainty,
        r.processing_sec,
        r.last_modified_utc
    from raw_inpaint r
    left join model_versions mv
        on r.config_hash = mv.config_hash
    where r.pass_id is not null
    qualify row_number() over (
        partition by r.pass_id
        order by r.generated_at_utc desc nulls last, r.last_modified_utc desc nulls last, r.s3_key desc
    ) = 1
)
select * from deduped
