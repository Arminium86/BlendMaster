from flask import request, jsonify

def grade_targets():
    data = request.json
    
    # Extract grade target configurations for both periods
    period_1_targets = data.get('period_1_targets')
    period_2_targets = data.get('period_2_targets')

    # Validate the inputs
    if not period_1_targets or not period_2_targets:
        return jsonify({"status": "error", "message": "Missing grade targets for one or both periods."})

    # Simulate saving to a database (or in-memory storage)
    grade_data = {
        "period_1_targets": period_1_targets,
        "period_2_targets": period_2_targets
    }

    # Respond with success
    return jsonify({"status": "success", "data": grade_data, "message": "Grade targets and blend configurations saved successfully!"})
