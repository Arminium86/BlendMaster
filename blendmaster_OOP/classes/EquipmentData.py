class EquipmentData:
    def __init__(self, name, priority_preplan, priority_period_1, priority_period_2, rate_preplan, rate_period_1, rate_period_2, period_values=None):
        self._name = name
        self._priority_preplan = priority_preplan
        self._priority_period_1 = priority_period_1
        self._priority_period_2 = priority_period_2
        self._rate_preplan = rate_preplan
        self._rate_period_1 = rate_period_1
        self._rate_period_2 = rate_period_2
        self._period_values = dict(period_values or {})

    # Getters
    @property
    def name(self):
        return self._name

    @property
    def priority_preplan(self):
        return self._priority_preplan

    @property
    def priority_period_1(self):
        return self._priority_period_1

    @property
    def priority_period_2(self):
        return self._priority_period_2

    @property
    def rate_preplan(self):
        return self._rate_preplan

    @property
    def rate_period_1(self):
        return self._rate_period_1

    @property
    def rate_period_2(self):
        return self._rate_period_2

    # Setters
    @name.setter
    def name(self, value):
        self._name = value

    @priority_preplan.setter
    def priority_preplan(self, value):
        self._priority_preplan = value

    @priority_period_1.setter
    def priority_period_1(self, value):
        self._priority_period_1 = value

    @priority_period_2.setter
    def priority_period_2(self, value):
        self._priority_period_2 = value

    @rate_preplan.setter
    def rate_preplan(self, value):
        self._rate_preplan = value

    @rate_period_1.setter
    def rate_period_1(self, value):
        self._rate_period_1 = value

    @rate_period_2.setter
    def rate_period_2(self, value):
        self._rate_period_2 = value

    # Method to retrieve the original dictionary
    def to_dict(self):
        result = {
            "name": self._name,
            "priority_preplan": self._priority_preplan,
            "priority_period_1": self._priority_period_1,
            "priority_period_2": self._priority_period_2,
            "rate_preplan": self._rate_preplan,
            "rate_period_1": self._rate_period_1,
            "rate_period_2": self._rate_period_2,
        }
        result.update(self._period_values)
        return result
