"""Create and query the configured BlendMaster planning periods."""

from datetime import timedelta


class PeriodManager:
    DEFAULT_PERIOD_COUNT = 3
    MIN_PERIOD_COUNT = 3
    MAX_PERIOD_COUNT = 31

    def __init__(self, period_count=DEFAULT_PERIOD_COUNT):
        self.period_count = self.normalize_period_count(period_count)
        self.periods = {}

    @classmethod
    def normalize_period_count(cls, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = cls.DEFAULT_PERIOD_COUNT
        return min(max(value, cls.MIN_PERIOD_COUNT), cls.MAX_PERIOD_COUNT)

    @staticmethod
    def key_for_index(index):
        return "preplan" if index == 0 else f"period_{index}"

    @staticmethod
    def label_for_index(index):
        return "Preplan" if index == 0 else f"Period_{index}"

    @classmethod
    def period_keys_for_count(cls, period_count):
        count = cls.normalize_period_count(period_count)
        return [cls.key_for_index(index) for index in range(count)]

    @classmethod
    def period_labels_for_count(cls, period_count):
        count = cls.normalize_period_count(period_count)
        return [cls.label_for_index(index) for index in range(count)]

    def period_keys(self):
        return self.period_keys_for_count(self.period_count)

    def period_labels(self):
        return self.period_labels_for_count(self.period_count)

    def calculate_periods(self, start_time, period_count=None):
        if period_count is not None:
            self.period_count = self.normalize_period_count(period_count)

        if 6 <= start_time.hour < 18:
            preplan_end = start_time.replace(
                hour=18, minute=0, second=0, microsecond=0
            )
        elif start_time.hour >= 18:
            preplan_end = start_time.replace(
                hour=6, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
        else:
            preplan_end = start_time.replace(
                hour=6, minute=0, second=0, microsecond=0
            )

        periods = {}
        period_start = start_time
        for index, period_key in enumerate(self.period_keys()):
            period_end = (
                preplan_end
                if index == 0
                else period_start + timedelta(hours=12)
            )
            periods[f"{period_key}_start"] = period_start
            periods[f"{period_key}_end"] = period_end
            periods[f"{period_key}_duration"] = (
                period_end - period_start
            ).total_seconds() / 3600
            period_start = period_end

        self.periods = periods
        return periods

    def get_periods(self):
        return self.periods
    def horizon_end(self):
        if not self.periods:
            return None
        return self.periods[f"{self.period_keys()[-1]}_end"]

    def period_for_datetime(self, value):
        for period_key in self.period_keys():
            if (
                self.periods[f"{period_key}_start"]
                <= value
                < self.periods[f"{period_key}_end"]
            ):
                return period_key
        return None

    def next_period(self, period_key):
        try:
            index = self.period_keys().index(period_key)
        except ValueError:
            return None
        next_index = index + 1
        if next_index >= self.period_count:
            return None
        return self.key_for_index(next_index)
