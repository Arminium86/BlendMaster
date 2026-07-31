class StockpileData:
    def __init__(
        self, name, balance, state_preplan, state_period_1, state_period_2,
        max_quantity_preplan, max_quantity_period_1, max_quantity_period_2,
        cost_preplan, cost_period_1, cost_period_2,
        cash_preplan, cash_period_1, cash_period_2,
        equipment, reclaim_threshold, grade_fe, grade_si, grade_al, grade_p, grade_mn, auto_turnover_datetime, is_ready, is_AMT,
        aps_brand=None, aps_brand_proportions=None, aps_brand_tonnes=None,
        max_reclaim_rate=None, period_values=None, grade_streams=None
    ):
        self._name = name
        self._balance = balance
        self._state_preplan = state_preplan
        self._state_period_1 = state_period_1
        self._state_period_2 = state_period_2
        self._max_quantity_preplan = max_quantity_preplan
        self._max_quantity_period_1 = max_quantity_period_1
        self._max_quantity_period_2 = max_quantity_period_2
        self._cost_preplan = cost_preplan
        self._cost_period_1 = cost_period_1
        self._cost_period_2 = cost_period_2
        self._cash_preplan = cash_preplan
        self._cash_period_1 = cash_period_1
        self._cash_period_2 = cash_period_2
        self._equipment = equipment
        self._reclaim_threshold = reclaim_threshold
        self._grade_fe = grade_fe
        self._grade_si = grade_si
        self._grade_al = grade_al
        self._grade_p = grade_p
        self._grade_mn = grade_mn
        self._auto_turnover_datetime = auto_turnover_datetime
        self._is_ready = is_ready
        self._is_AMT = is_AMT
        self._aps_brand = aps_brand or ""
        self._aps_brand_proportions = aps_brand_proportions or {}
        self._aps_brand_tonnes = aps_brand_tonnes or {}
        self._max_reclaim_rate = max_reclaim_rate
        self._period_values = dict(period_values or {})
        self._grade_streams = grade_streams

    # Getters
    @property
    def name(self):
        return self._name

    @property
    def balance(self):
        return self._balance

    @property
    def state_preplan(self):
        return self._state_preplan

    @property
    def state_period_1(self):
        return self._state_period_1

    @property
    def state_period_2(self):
        return self._state_period_2

    @property
    def max_quantity_preplan(self):
        return self._max_quantity_preplan

    @property
    def max_quantity_period_1(self):
        return self._max_quantity_period_1

    @property
    def max_quantity_period_2(self):
        return self._max_quantity_period_2

    @property
    def cost_preplan(self):
        return self._cost_preplan

    @property
    def cost_period_1(self):
        return self._cost_period_1

    @property
    def cost_period_2(self):
        return self._cost_period_2

    @property
    def cash_preplan(self):
        return self._cash_preplan

    @property
    def cash_period_1(self):
        return self._cash_period_1

    @property
    def cash_period_2(self):
        return self._cash_period_2

    @property
    def equipment(self):
        return self._equipment

    @property
    def reclaim_threshold(self):
        return self._reclaim_threshold

    @property
    def grade_fe(self):
        return self._grade_fe

    @property
    def grade_si(self):
        return self._grade_si

    @property
    def grade_al(self):
        return self._grade_al

    @property
    def grade_p(self):
        return self._grade_p

    @property
    def grade_mn(self):
        return self._grade_mn

    @property
    def auto_turnover_datetime(self):
        return self._auto_turnover_datetime

    @property
    def is_ready(self):
        return self._is_ready
    
    @property
    def is_AMT(self):
        return self._is_AMT

    @property
    def aps_brand(self):
        return self._aps_brand

    @property
    def aps_brand_proportions(self):
        return self._aps_brand_proportions

    @property
    def aps_brand_tonnes(self):
        return self._aps_brand_tonnes

    @property
    def max_reclaim_rate(self):
        return self._max_reclaim_rate

    @property
    def grade_streams(self):
        return self._grade_streams
    
    # Setters
    @name.setter
    def name(self, value):
        self._name = value

    @balance.setter
    def balance(self, value):
        self._balance = value

    @state_preplan.setter
    def state_preplan(self, value):
        self._state_preplan = value

    @state_period_1.setter
    def state_period_1(self, value):
        self._state_period_1 = value

    @state_period_2.setter
    def state_period_2(self, value):
        self._state_period_2 = value

    @max_quantity_preplan.setter
    def max_quantity_preplan(self, value):
        self._max_quantity_preplan = value

    @max_quantity_period_1.setter
    def max_quantity_period_1(self, value):
        self._max_quantity_period_1 = value

    @max_quantity_period_2.setter
    def max_quantity_period_2(self, value):
        self._max_quantity_period_2 = value

    @cost_preplan.setter
    def cost_preplan(self, value):
        self._cost_preplan = value

    @cost_period_1.setter
    def cost_period_1(self, value):
        self._cost_period_1 = value

    @cost_period_2.setter
    def cost_period_2(self, value):
        self._cost_period_2 = value

    @cash_preplan.setter
    def cash_preplan(self, value):
        self._cash_preplan = value

    @cash_period_1.setter
    def cash_period_1(self, value):
        self._cash_period_1 = value

    @cash_period_2.setter
    def cash_period_2(self, value):
        self._cash_period_2 = value

    @equipment.setter
    def equipment(self, value):
        self._equipment = value

    @reclaim_threshold.setter
    def reclaim_threshold(self, value):
        self._reclaim_threshold = value

    @grade_fe.setter
    def grade_fe(self, value):
        self._grade_fe = value

    @grade_si.setter
    def grade_si(self, value):
        self._grade_si = value

    @grade_al.setter
    def grade_al(self, value):
        self._grade_al = value

    @grade_p.setter
    def grade_p(self, value):
        self._grade_p = value

    @grade_mn.setter
    def grade_mn(self, value):
        self._grade_mn = value

    @auto_turnover_datetime.setter
    def auto_turnover_datetime(self, value):
        self._auto_turnover_datetime = value
        
    @is_ready.setter
    def is_ready(self, value):
        self._is_ready = value

    @is_AMT.setter
    def is_AMT(self, value):
        self._is_AMT = value

    @aps_brand.setter
    def aps_brand(self, value):
        self._aps_brand = value or ""

    @aps_brand_proportions.setter
    def aps_brand_proportions(self, value):
        self._aps_brand_proportions = value or {}

    @aps_brand_tonnes.setter
    def aps_brand_tonnes(self, value):
        self._aps_brand_tonnes = value or {}

    @max_reclaim_rate.setter
    def max_reclaim_rate(self, value):
        self._max_reclaim_rate = value

    @grade_streams.setter
    def grade_streams(self, value):
        self._grade_streams = value

    # Method to retrieve the original dictionary
    def to_dict(self):
        result = {
            "name": self._name,
            "balance": self._balance,
            "state_preplan": self._state_preplan,
            "state_period_1": self._state_period_1,
            "state_period_2": self._state_period_2,
            "max_quantity_preplan": self._max_quantity_preplan,
            "max_quantity_period_1": self._max_quantity_period_1,
            "max_quantity_period_2": self._max_quantity_period_2,
            "cost_preplan": self._cost_preplan,
            "cost_period_1": self._cost_period_1,
            "cost_period_2": self._cost_period_2,
            "cash_preplan": self._cash_preplan,
            "cash_period_1": self._cash_period_1,
            "cash_period_2": self._cash_period_2,
            "equipment": self._equipment,
            "reclaim_threshold": self._reclaim_threshold,
            "grade_fe": self._grade_fe,
            "grade_si": self._grade_si,
            "grade_al": self._grade_al,
            "grade_p": self._grade_p,
            "grade_mn": self._grade_mn,
            "auto_turnover_datetime": self._auto_turnover_datetime,
            "is_ready": self._is_ready,
            "is_AMT": self._is_AMT,
            "aps_brand": self._aps_brand,
            "aps_brand_proportions": self._aps_brand_proportions,
            "aps_brand_tonnes": self._aps_brand_tonnes,
            "max_reclaim_rate": self._max_reclaim_rate,
            "grade_streams": self._grade_streams,
        }
        result.update(self._period_values)
        return result
