import types
from classes.Optimizer import Optimizer
from classes.EventData import EventData


def test_selected_stockpiles_contribute_non_zero_tonnes():
    event1 = EventData(
        stockpile="SP1",
        grade_block=None,
        event_type="stockpile",
        equipment="eq",
        cost=0,
        cash=0,
        rate=100,
        grade_fe=0,
        grade_si=0,
        grade_al=0,
        grade_p=0,
        grade_mn=0,
        balance=100,
        max_quantity=100,
        reclaim_threshold=0,
        state="ready",
        auto_turnover_datetime=None,
    )
    event2 = EventData(
        stockpile="SP2",
        grade_block=None,
        event_type="stockpile",
        equipment="eq",
        cost=0,
        cash=0,
        rate=100,
        grade_fe=0,
        grade_si=0,
        grade_al=0,
        grade_p=0,
        grade_mn=0,
        balance=100,
        max_quantity=100,
        reclaim_threshold=0,
        state="ready",
        auto_turnover_datetime=None,
    )

    event_pool = [event1, event2]

    period_crusher_target = {
        "crusher_rate": 100,
        "direct_feed_ratio_max": 0,
        "direct_feed_ratio_min": 0,
        "target_fe_min": 0,
        "target_fe_max": 100,
        "target_si_min": 0,
        "target_si_max": 100,
        "target_al_min": 0,
        "target_al_max": 100,
        "target_p_min": 0,
        "target_p_max": 100,
        "target_mn_min": 0,
        "target_mn_max": 100,
    }

    periods = types.SimpleNamespace(get_periods=lambda: {"preplan_duration": 1})

    optimizer = Optimizer()
    result = optimizer.run_blending_optimization(
        event_pool,
        period_crusher_target,
        steady_state_duration=1,
        steady_state_controller_source=None,
        steady_state_controller_tonnes=None,
        periods=periods,
        period_tracker="preplan",
        min_stockpiles=2,
        max_stockpiles=2,
    )

    assert result["Linprog_result_object"].success
    xs = result["Linprog_result_object"].x
    assert xs[0] > 0 and xs[1] > 0
