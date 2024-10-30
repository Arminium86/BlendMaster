def crusher_targets(crusher_target, period):
    # Get the Fe grade target for the current period
    target_fe_min = crusher_target[period]["target_fe_min"]
    target_fe_max = crusher_target[period]["target_fe_max"]
    # Get the crusher rate for the current period
    crusher_rate = crusher_target[period]["crusher_rate"]
    
    # Return both the Fe grade target and crusher rate
    return {
        "target_fe_min": target_fe_min,
        "target_fe_max": target_fe_max,
        "crusher_rate": crusher_rate
    }

