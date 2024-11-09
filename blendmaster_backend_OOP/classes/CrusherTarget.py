# This simply returns crusher targets by period
class CrusherTarget:
    def __init__(self, crusher_targets):
        self.crusher_targets = crusher_targets

    def get_targets(self, period):
        """Return crusher target information for a given period."""
        return self.crusher_targets[period]
