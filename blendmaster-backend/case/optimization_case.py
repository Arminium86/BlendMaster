import sys
import os
# Add the project root directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), r'C:\BlendMaster\blendmaster-backend\routes')))

from routes.optimization_engine import run_with_dynamic_steady_state
from routes.event_generator import generate_event_pool
from routes.crusher_targets import crusher_targets
from routes.time_handler import calculate_periods
from case.input import stockpile_data, grade_block_data, crusher_rates, grade_targets, equipment_data


# Case for blending optimization
def case_run_blending_optimization():

    periods = calculate_periods()
    event_pool = generate_event_pool(stockpile_data, equipment_data, grade_block_data, "preplan")
    crusher_target = crusher_targets(grade_targets, crusher_rates, "preplan")
    result = run_with_dynamic_steady_state(event_pool, crusher_target, "preplan", periods)
    #print("Optimization result:", result)
    #print("Event Pool:", event_pool)
    print(f"Preplan and periods calculated successfully: {periods}\n")
    print(f"status: {result['status']}\n")
    print(f"steady state duration: {result['steady state duration']}\n")
    print(f"Actual Crusher Tonnes: {result['Actual Crusher Tonnes']}\n")
    print(f"Actual Crusher Fe Grade: {result['Actual Crusher Fe Grade']}\n")
    print(f"Crusher Fe Grade Target (Min): {result['Crusher Fe Grade Target (Min)']}\n")
    print(f"Crusher Fe Grade Target (max): {result['Crusher Fe Grade Target (max)']}\n")
    for event in result["outcome"]:
        print(f"Event {event['event_number']}:\n{event['details']}")
    
# Call your case functions
if __name__ == "__main__":

    case_run_blending_optimization()