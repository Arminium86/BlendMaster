from flask import Flask, jsonify, request
from setup.site import setup_site
from setup.stockpile import stockpile_rules
from setup.equipment import equipment_rates
from setup.grades import grade_targets

app = Flask(__name__)

# Register setup routes
app.add_url_rule('/setup/site', 'setup_site', setup_site, methods=['POST'])
app.add_url_rule('/setup/stockpile-rules', 'stockpile_rules', stockpile_rules, methods=['POST'])
app.add_url_rule('/setup/equipment-rates', 'equipment_rates', equipment_rates, methods=['POST'])
app.add_url_rule('/setup/grade-targets', 'grade_targets', grade_targets, methods=['POST'])

@app.route('/')
def index():
    return jsonify({"message": "Welcome to BlendMaster API!"})

if __name__ == '__main__':
    app.run(debug=True)
