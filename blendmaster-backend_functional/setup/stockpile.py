from flask import request, jsonify

def stockpile_rules():
    data = request.json
    
    # Extract stockpile rules
    stockpile_rules_list = data.get('stockpiles')

    # Process and validate each stockpile rule
    for rule in stockpile_rules_list:
        source = rule.get('source')
        use = rule.get('use')
        reclaim_threshold = rule.get('reclaim_threshold')
        build_threshold = rule.get('build_threshold')
        equipment = rule.get('equipment')

        # Perform validation and processing (simplified here)
        if not source or reclaim_threshold is None:
            return jsonify({"status": "error", "message": f"Missing data for stockpile {source}"})

    # Respond with success
    return jsonify({"status": "success", "data": stockpile_rules_list, "message": "Stockpile rules saved successfully!"})
