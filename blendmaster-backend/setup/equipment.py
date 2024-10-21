from flask import request, jsonify

def equipment_rates():
    data = request.json

    # Extract rates and priorities for each 12-hour period
    period_1_rates = data.get('period_1_rates')
    period_2_rates = data.get('period_2_rates')

    # Simulate storing the rates in memory (or a database)
    equipment_data = {
        "period_1_rates": period_1_rates,
        "period_2_rates": period_2_rates
    }

    # Respond with success
    return jsonify({"status": "success", "data": equipment_data, "message": "Equipment rates and priorities saved successfully!"})
