def generate_event_pool(stockpile_data, equipment_data, grade_block_data, period):
    events = []

    # Loop through each stockpile and match it with all reclaimers it can be reclaimed by
    for stockpile in stockpile_data:
        stockpile_priority = stockpile[f"priority_{period}"]  # Get stockpile priority for the period
        for equipment in equipment_data:
            if equipment["name"] in stockpile["equipment"]:  # Based on movement rules
                equipment_priority = equipment[f"priority_{period}"]  # Get equipment priority for the period
                reclaim_rate = equipment[f"rate_{period}"]  # Get reclaim rate for the period
                
                events.append({
                    "stockpile": stockpile["name"],
                    "equipment": equipment["name"],
                    "priority": stockpile_priority + equipment_priority,  # Combined priority for cost minimization
                    "rate": reclaim_rate,  # Reclaim rate (time-dependent)
                    "grade_fe": stockpile["grade_fe"],
                    "balance": stockpile["balance"]
                })
    
    # Add direct tip opportunities from grade blocks
    for grade_block in grade_block_data:
        for equipment in equipment_data:
            if (equipment["name"] in grade_block["equipment"]) and ("EX" in equipment["name"]):  # Ensure only excavators (diggers) are used for grade blocks
                equipment_priority = equipment[f"priority_{period}"]  # Get digger priority for the period
                reclaim_rate = equipment[f"rate_{period}"]  # Get reclaim rate for the period

                events.append({
                    "grade_block": grade_block["name"],
                    "equipment": equipment["name"],
                    "priority": equipment_priority,  # Diggers have no stockpile priority
                    "rate": reclaim_rate,  # Reclaim rate (time-dependent)
                    "grade_fe": grade_block["grade_fe"],
                    "balance": grade_block["balance"]
                })

    return events