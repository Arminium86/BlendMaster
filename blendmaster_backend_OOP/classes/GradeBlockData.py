class GradeBlockData:
    def __init__(
        self,
        name,
        balance,
        max_quantity_preplan,
        max_quantity_period_1,
        max_quantity_period_2,
        cost_preplan,
        cost_period_1,
        cost_period_2,
        cash_preplan,
        cash_period_1,
        cash_period_2,
        equipment,
        grade_fe,
        grade_si,
        grade_al,
        grade_p,
        grade_mn
    ):
        self._name = name
        self._balance = balance
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
        self._grade_fe = grade_fe
        self._grade_si = grade_si
        self._grade_al = grade_al
        self._grade_p = grade_p
        self._grade_mn = grade_mn
    
    # Getters
    @property
    def name(self):
        return self._name

    @property
    def balance(self):
        return self._balance

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
    def grade_fe(self):
        return self._grade_fe
   
    @property
    def grade_si(self):
        return self._grade_si
   
    @property
    def grade_al(self):
        return self._grade_al
   
    @property
    def grade_mn(self):
        return self._grade_mn
   
    @property
    def grade_p(self):
        return self._grade_p
    

    # Setters
    @name.setter
    def name(self, value):
        self._name = value

    @balance.setter
    def balance(self, value):
        self._balance = value

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
 
    # Method to retrieve the original dictionary
    def to_dict(self):
        return {
            "name": self._name,
            "balance": self._balance,
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
            "grade_fe": self._grade_fe,
            "grade_si": self._grade_si,
            "grade_al": self._grade_al,
            "grade_p": self._grade_p,
            "grade_mn": self._grade_mn,
        }
