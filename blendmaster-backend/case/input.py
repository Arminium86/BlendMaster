stockpile_data = [
    {"id": 1, "name": "SP1", "balance": 100000, "priority_preplan": 1, "priority_period_1": 0, "priority_period_2": 0, "use": True, "equipment": ["RC"], "reclaim_threshold": 500, "grade_fe": 58.0},
    {"id": 2, "name": "SP2", "balance": 100000, "priority_preplan": 2, "priority_period_1": 0, "priority_period_2": 0, "use": True, "equipment": ["RC"], "reclaim_threshold": 1000, "grade_fe": 60.0},
    {"id": 3, "name": "SP3", "balance": 100000, "priority_preplan": 3, "priority_period_1": 0, "priority_period_2": 0, "use": True, "equipment": ["RC"], "reclaim_threshold": 0, "grade_fe": 60.0}
]
grade_block_data = [
    {"id": 1, "name": "GB1", "balance": 100000, "use": True, "equipment": ["EX"], "grade_fe": 58.0},
    {"id": 2, "name": "GB2", "balance": 100000, "use": True, "equipment": ["EX"],  "grade_fe": 57.5}
]
equipment_data = [
    {"id": 1, "name": "RC", "priority_preplan": 1, "priority_period_1": 1, "priority_period_2": 1, "rate_preplan": 3000, "rate_period_1": 1200, "rate_period_2": 1100},
    {"id": 2, "name": "EX", "priority_preplan": 1, "priority_period_1": 1, "priority_period_2": 1, "rate_preplan": 3000, "rate_period_1": 700, "rate_period_2": 750}
]
    
grade_targets = {
    "preplan": {"target_fe_min": 58.0, "target_fe_max": 59.0},
    "period_1": {"target_fe_min": 60.0, "target_fe_max": 62.0},
    "period_2": {"target_fe_min": 60.0, "target_fe_max": 61.5}
}

crusher_rates = {
    
        "crusher_rate": {"preplan": 6000, "period_1": 6000, "period_2": 6000}
}