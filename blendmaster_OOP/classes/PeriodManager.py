# This generate 3 periods whenever the progrsm runs: preplan, period_1, and period_2. Preplan is from current time to 6AM or 6PM whichever first
# which is followed by two 12 hour periods
from datetime import datetime, timedelta

class PeriodManager:
    def __init__(self):
        self.periods = {}

    def calculate_periods(self, start_time):
        now = start_time

        # Define next 6AM and 6PM
        if now.hour >= 6 and now.hour < 18: 
            next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
            next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

        elif now.hour >= 18:
            next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
            next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=1)

        else:  
            next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
            next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

        # Preplan ends at the closest 6AM or 6PM
        preplan_end = min(next_6am, next_6pm)

        # Period 1 and Period 2 follow the preplan period
        period_1_start = preplan_end
        period_1_end = period_1_start + timedelta(hours=12)
        period_2_start = period_1_end
        period_2_end = period_2_start + timedelta(hours=12)

        self.periods = {
            "preplan_start": now,
            "preplan_end": preplan_end,
            "preplan_duration": (preplan_end - now).total_seconds() / 3600,
            "period_1_start": period_1_start,
            "period_1_end": period_1_end,
            "period_1_duration": (period_1_end - period_1_start).total_seconds() / 3600,
            "period_2_start": period_2_start,
            "period_2_end": period_2_end,
            "period_2_duration": (period_2_end - period_2_start).total_seconds() / 3600,
        }

    def get_periods(self):
        return self.periods