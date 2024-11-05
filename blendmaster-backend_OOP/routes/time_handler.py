# Define the periods (preplan, Period 1, Period 2)
from datetime import datetime, timedelta


def calculate_periods():
    now = datetime.now()
    
    # Define next 6AM and 6PM, adjusting for current time
    if now.hour >= 6 and now.hour < 18:  # Between 6AM and 6PM
        next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
        next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

    elif now.hour >= 18:  # Between 6PM and midnight
        next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
        next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=1)

    else:  # Between midnight and 6AM
        next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

    # Preplan ends at the closest 6AM or 6PM
    preplan_end = min(next_6am, next_6pm)

    # Period 1 and Period 2 follow the preplan period
    period_1_start = preplan_end
    period_1_end = period_1_start + timedelta(hours=12)
    period_2_start = period_1_end
    period_2_end = period_2_start + timedelta(hours=12)

    return {
        "preplan_start": now,
        "preplan_end": preplan_end,
        "period_1_start": period_1_start,
        "period_1_end": period_1_end,
        "period_2_start": period_2_start,
        "period_2_end": period_2_end
    }

# Track stockpile depletion and update steady state duration
def depletion_time_tracker(events, steady_state_duration, selected_tonnes):
    """
    Track if any stockpile or grade block will deplete sooner than the given steady state duration.
    
    Args:
    - events: List of events from the optimization result, containing information about actual reclaimed tonnes and rates.
    - steady_state_duration: The initial steady state duration in hours.
    - selected_tonnes: The actual tonnes selected by the solver for each event.
    
    Returns:
    - The updated steady state duration (the minimum of the original steady state or any early depletion).
    """
    updated_duration = steady_state_duration
    
    for i, event in enumerate(events):
        if selected_tonnes[i] == event["balance"]:
            actual_tonnes = selected_tonnes[i]  # Actual tonnes selected by the solver
            rate = event["rate"]  # Equipment rate for reclaim or digging

            # Calculate time to depletion based on the actual selected tonnes
            time_to_depletion = actual_tonnes / rate

            # If the event will deplete sooner than the current steady state, update the steady state duration
            if time_to_depletion < updated_duration:
                updated_duration = time_to_depletion
    
    return updated_duration


