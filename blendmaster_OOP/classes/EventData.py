class EventData:
    def __init__(
        self,
        stockpile,
        grade_block,
        event_type,
        equipment,
        cost,
        cash,
        rate,
        grade_fe,
        grade_si,
        grade_al,
        grade_p,
        grade_mn,
        balance,
        max_quantity,
        reclaim_threshold,
        state,
        auto_turnover_datetime,
        is_amt=False,
        source_name=None,
        delivered_datetime=None,
        aps_brand=None,
        aps_brand_proportions=None,
        grade_streams=None,
    ):
        self._stockpile = stockpile
        self._grade_block = grade_block
        self._type = event_type
        self._equipment = equipment
        self._cost = cost
        self._cash = cash
        self._rate = rate
        self._grade_fe = grade_fe
        self._grade_si = grade_si
        self._grade_al = grade_al
        self._grade_p = grade_p
        self._grade_mn = grade_mn
        self._balance = balance
        self._max_quantity = max_quantity
        self._reclaim_threshold = reclaim_threshold
        self._state = state
        self._auto_turnover_datetime = auto_turnover_datetime
        self._is_amt = is_amt
        self._source_name = source_name
        self._delivered_datetime = delivered_datetime
        self._aps_brand = aps_brand or ""
        self._aps_brand_proportions = aps_brand_proportions or {}
        self._grade_streams = grade_streams
        self._selected_grade_stream = None
        self._selected_grade_brand = None
        self._grade_stream_warnings = []

    # Getters
    @property
    def stockpile(self):
        return self._stockpile
    
    @property
    def grade_block(self):
        return self._grade_block

    @property
    def type(self):
        return self._type

    @property
    def equipment(self):
        return self._equipment

    @property
    def cost(self):
        return self._cost

    @property
    def cash(self):
        return self._cash

    @property
    def rate(self):
        return self._rate

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
    def balance(self):
        return self._balance

    @property
    def max_quantity(self):
        return self._max_quantity

    @property
    def reclaim_threshold(self):
        return self._reclaim_threshold

    @property
    def state(self):
        return self._state

    @property
    def auto_turnover_datetime(self):
        return self._auto_turnover_datetime

    @property
    def is_amt(self):
        return self._is_amt

    @property
    def source_name(self):
        return self._source_name

    @property
    def delivered_datetime(self):
        return self._delivered_datetime

    @property
    def aps_brand(self):
        return self._aps_brand

    @property
    def aps_brand_proportions(self):
        return self._aps_brand_proportions

    @property
    def grade_streams(self):
        return self._grade_streams

    @property
    def selected_grade_stream(self):
        return self._selected_grade_stream

    @property
    def selected_grade_brand(self):
        return self._selected_grade_brand

    @property
    def grade_stream_warnings(self):
        return self._grade_stream_warnings

    # Setters
    @stockpile.setter
    def stockpile(self, value):
        self._stockpile = value

    @grade_block.setter
    def grade_block(self, value):
        self._grade_block = value

    @type.setter
    def type(self, value):
        self._type = value

    @equipment.setter
    def equipment(self, value):
        self._equipment = value

    @cost.setter
    def cost(self, value):
        self._cost = value

    @cash.setter
    def cash(self, value):
        self._cash = value

    @rate.setter
    def rate(self, value):
        self._rate = value

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

    @balance.setter
    def balance(self, value):
        self._balance = value

    @max_quantity.setter
    def max_quantity(self, value):
        self._max_quantity = value

    @reclaim_threshold.setter
    def reclaim_threshold(self, value):
        self._reclaim_threshold = value

    @state.setter
    def state(self, value):
        self._state = value

    @auto_turnover_datetime.setter
    def auto_turnover_datetime(self, value):
        self._auto_turnover_datetime = value

    @is_amt.setter
    def is_amt(self, value):
        self._is_amt = value

    @source_name.setter
    def source_name(self, value):
        self._source_name = value

    @delivered_datetime.setter
    def delivered_datetime(self, value):
        self._delivered_datetime = value

    @aps_brand.setter
    def aps_brand(self, value):
        self._aps_brand = value or ""

    @aps_brand_proportions.setter
    def aps_brand_proportions(self, value):
        self._aps_brand_proportions = value or {}

    @grade_streams.setter
    def grade_streams(self, value):
        self._grade_streams = value

    @selected_grade_stream.setter
    def selected_grade_stream(self, value):
        self._selected_grade_stream = value

    @selected_grade_brand.setter
    def selected_grade_brand(self, value):
        self._selected_grade_brand = value

    @grade_stream_warnings.setter
    def grade_stream_warnings(self, value):
        self._grade_stream_warnings = value or []
    
     # Boolean methods
    @property
    def is_stockpile(self) -> bool:
        """
        Check if the event type is 'stockpile'.
        """
        return self._type == "stockpile"

    @property
    def is_grade_block(self) -> bool:
        """
        Check if the event type is 'grade_block'.
        """
        return self._type == "grade_block"
    
    # Convert to dictionary
    def to_dict(self):
        return {
            "stockpile": self._stockpile,
            "grade_block": self._grade_block,
            "type": self._type,
            "equipment": self._equipment,
            "cost": self._cost,
            "cash": self._cash,
            "rate": self._rate,
            "grade_fe": self._grade_fe,
            "grade_si": self._grade_si,
            "grade_al": self._grade_al,
            "grade_p": self._grade_p,
            "grade_mn": self._grade_mn,
            "balance": self._balance,
            "max_quantity": self._max_quantity,
            "reclaim_threshold": self._reclaim_threshold,
            "state": self._state,
            "auto_turnover_datetime": self._auto_turnover_datetime,
            "is_amt": self._is_amt,
            "source_name": self._source_name,
            "delivered_datetime": self._delivered_datetime,
            "aps_brand": self._aps_brand,
            "aps_brand_proportions": self._aps_brand_proportions,
            "grade_streams": self._grade_streams,
            "selected_grade_stream": self._selected_grade_stream,
            "selected_grade_brand": self._selected_grade_brand,
            "grade_stream_warnings": self._grade_stream_warnings,
        }
