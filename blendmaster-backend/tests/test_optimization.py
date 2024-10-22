import sys
import os

# Add the project root directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), r'C:\BlendMaster\blendmaster-backend\routes')))

from routes.optimization import calculate_periods, run_blending_optimization

# Test case for periods
def test_calculate_periods():
    periods = calculate_periods()
    assert periods["preplan_end"] is not None
    print("Preplan and periods calculated successfully:", periods)

# Test case for blending optimization
def test_run_blending_optimization():
    stockpile_data = [
    {"id": 1, "name": "SP1", "tonnage": 2000, "priority": 1, "use": True, "reclaim_threshold": 500, "grade_fe": 62.5},
    {"id": 2, "name": "SP2", "tonnage": 3000, "priority": 2, "use": True, "reclaim_threshold": 1000, "grade_fe": 61.0},
    {"id": 3, "name": "SP3", "tonnage": 1500, "priority": 3, "use": True, "reclaim_threshold": 0, "grade_fe": 60.0}
]
    grade_blocks = [
    {"id": 1, "name": "GB1", "tonnage": 500, "use": True, "grade_fe": 65.0},
    {"id": 2, "name": "GB2", "tonnage": 300, "use": True, "grade_fe": 64.5}
]
    reclaimer_data = [
    {"id": 1, "name": "RC1", "rate_preplan": 1000, "rate_period_1": 1200, "rate_period_2": 1100},
    {"id": 2, "name": "RC2", "rate_preplan": 1500, "rate_period_1": 1400, "rate_period_2": 1300},
    {"id": 3, "name": "RC3", "rate_preplan": 500,  "rate_period_1": 600,  "rate_period_2": 700}
]
    digger_data = [
    {"id": 1, "name": "EX1", "rate_preplan": 800, "rate_period_1": 1000, "rate_period_2": 900},
    {"id": 2, "name": "EX2", "rate_preplan": 600, "rate_period_1": 700, "rate_period_2": 750}
]
    user_inputs = {
    "grade_targets": {
        "preplan": {"target_fe": 62.0},
        "period_1": {"target_fe": 61.5},
        "period_2": {"target_fe": 60.5}
    },
    "crusher_rate": {
        "preplan": 3000,  # Tonnes/hour
        "period_1": 2500,
        "period_2": 2000
    }
}

    
    result = run_blending_optimization(stockpile_data, grade_blocks, reclaimer_data, digger_data, user_inputs, "preplan")
    assert result["status"] == "success"
    print("Optimization result:", result)

# Make sure to call your test functions if you're not using a test framework
if __name__ == "__main__":
    test_calculate_periods()
    test_run_blending_optimization()