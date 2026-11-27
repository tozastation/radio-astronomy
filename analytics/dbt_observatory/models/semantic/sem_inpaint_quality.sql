select
    coalesce(config_hash, 'unknown') as config_hash,
    max(model_name) as model_name,
    coalesce(model_mode, 'unknown') as model_mode,
    coalesce(notebook_ref, 'unknown') as notebook_ref,
    coalesce(patch_size, 64) as patch_size,
    count(*) as run_count,
    count(*) filter (where tier = 'Tier A') as tier_a_count,
    count(*) filter (where tier = 'Tier B') as tier_b_count,
    count(*) filter (where tier = 'Tier C') as tier_c_count,
    round(avg(seam_jump_l1), 4) as mean_seam_jump_l1,
    round(avg(mean_uncertainty), 4) as mean_uncertainty,
    round(100.0 * sum(case when is_single_band_dropout then 1 else 0 end) / nullif(count(*), 0), 2) as single_band_dropout_rate_pct,
    round(100.0 * sum(case when exceeds_patch_size then 1 else 0 end) / nullif(count(*), 0), 2) as large_gap_rate_pct,
    round(avg(missing_pct), 2) as mean_missing_pct,
    round(avg(partial_valid_pct), 2) as mean_partial_valid_pct,
    max(val_psnr_db) as val_psnr_db,
    max(val_sobel_pct) as val_sobel_pct
from {{ ref('fct_inpaint_runs') }}
where not coalesce(is_golden, false)
group by coalesce(config_hash, 'unknown'), coalesce(model_mode, 'unknown'), coalesce(notebook_ref, 'unknown'), coalesce(patch_size, 64)
