def crusher_targets(grade_targets, crusher_rates, period):
    # Get the Fe grade target for the current period
    fe_target_min = grade_targets[period]["target_fe_min"]
    fe_target_max = grade_targets[period]["target_fe_max"]
    # Get the crusher rate for the current period
    crusher_rate = crusher_rates["crusher_rate"][period]
    
    # Return both the Fe grade target and crusher rate
    return {
        "fe_target_min": fe_target_min,
        "fe_target_max": fe_target_max,
        "crusher_rate": crusher_rate
    }

