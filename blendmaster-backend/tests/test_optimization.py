import sys
import os

# Add the project root directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), r'C:\BlendMaster\blendmaster-backend\routes')))

from routes.optimization import calculate_periods, run_blending_optimization, generate_event_pool, crusher_targets

# Test case for periods
def test_calculate_periods():
    periods = calculate_periods()
    assert periods["preplan_end"] is not None
    print("Preplan and periods calculated successfully:", periods)

# Test case for blending optimization
def test_run_blending_optimization():
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

    periods = calculate_periods()
    event_pool = generate_event_pool(stockpile_data, equipment_data, grade_block_data, "preplan")
    crusher_target = crusher_targets(grade_targets, crusher_rates, "preplan")
    result = run_blending_optimization(event_pool, crusher_target, "preplan", periods)
    #print("Optimization result:", result)
    #print("Event Pool:", event_pool)
    print(f"status: {result['status']}\n")
    print(f"steady state duration: {result['steady state duration']}\n")
    print(f"Actual Crusher Tonnes: {result['Actual Crusher Tonnes']}\n")
    print(f"Actual Crusher Fe Grade: {result['Actual Crusher Fe Grade']}\n")
    print(f"Crusher Fe Grade Target (Min): {result['Crusher Fe Grade Target (Min)']}\n")
    print(f"Crusher Fe Grade Target (max): {result['Crusher Fe Grade Target (max)']}\n")
    for event in result["outcome"]:
        print(f"Event {event['event_number']}:\n{event['details']}")
    
# Make sure to call your test functions if you're not using a test framework
if __name__ == "__main__":
    test_calculate_periods()
    test_run_blending_optimization()