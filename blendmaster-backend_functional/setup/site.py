from flask import request, jsonify

def setup_site():
    data = request.json
    
    # Extract setup data
    operation = data.get('operation')
    crusher = data.get('crusher')
    reclaimers = data.get('reclaimers')
    direct_tip = data.get('direct_tip')
    diggers = data.get('diggers', None)  # Optional if direct_tip is False

    # Simulate saving to a database or in-memory store (for now)
    site_config = {
        "operation": operation,
        "crusher": crusher,
        "reclaimers": reclaimers,
        "direct_tip": direct_tip,
        "diggers": diggers
    }

    # Respond with success
    return jsonify({"status": "success", "data": site_config, "message": "Site configuration saved successfully!"})
