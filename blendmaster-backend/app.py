from flask import Flask, jsonify, request
from setup.site import setup_site
from setup.stockpile import stockpile_rules
from setup.equipment import equipment_rates
from setup.grades import grade_targets
from setup.snowflake_conn import get_snowflake_data  # Import Snowflake connection
from routes.optimization import calculate_periods, run_blending_optimization

app = Flask(__name__)

# Register setup routes
app.add_url_rule('/setup/site', 'setup_site', setup_site, methods=['POST'])
app.add_url_rule('/setup/stockpile-rules', 'stockpile_rules', stockpile_rules, methods=['POST'])
app.add_url_rule('/setup/equipment-rates', 'equipment_rates', equipment_rates, methods=['POST'])
app.add_url_rule('/setup/grade-targets', 'grade_targets', grade_targets, methods=['POST'])

# New route to test Snowflake connection
@app.route('/stockpile-test', methods=['GET'])
def test_snowflake_connection():
    query = "SELECT HUB, AREANAME, ROMAREANAME, TRANSACTIONDIRECTION, STOCKPILENAME, MATERIAL, PRODUCT, BALANCEWMT, TRANSACTIONDATETIME, FE_INSITU_WTAVG, SIO2_INSITU_WTAVG, AL2O3_INSITU_WTAVG, P_INSITU_WTAVG, MN_INSITU_WTAVG FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_STOCKPILE_TRANSACTIONS WHERE TRANSACTIONDIRECTION IN ('Stack', 'Reclaim') AND STOCKPILETYPE IN ('RomStockpile') AND DATEDIFF(WEEK, TRANSACTIONDATETIME, CURRENT_DATE()) < 2 ORDER BY HUB, STOCKPILENAME, TRANSACTIONDATETIME"  # Example query
    
    try:
        stockpile_data = get_snowflake_data(query)  # Fetch data using Snowflake connection
        return jsonify({"status": "success", "data": stockpile_data})
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# Root route
@app.route('/')
def index():
    return jsonify({"message": "Welcome to BlendMaster API!"})

if __name__ == '__main__':
    app.run(debug=True)
