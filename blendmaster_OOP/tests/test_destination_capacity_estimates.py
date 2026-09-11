from copy import deepcopy
import unittest

from classes.DestinationProgress import remaining_2wp_estimates, ESTIMATED_CAPACITY_BASIS
from classes.PrimaryDestinationAllocator import PrimaryDestinationAllocator
from tests.test_primary_destination_allocator import context, payload, transactions
from classes.PrimaryDestinationAllocator import allocate_final_plan


class DestinationCapacityEstimateTests(unittest.TestCase):
    def engine(self, ctx, start="2026-09-08T08:30:00"):
        return PrimaryDestinationAllocator(ctx["order"], ctx["activity"], ctx["settings"], scenario_start=start)

    def test_excludes_past_and_later_builds_and_prorates_crossing_interval(self):
        ctx = context(None)
        first = ctx["order"]["audit"][0]["instance_id"]
        later = ctx["order"]["audit"][4]["instance_id"]
        values = remaining_2wp_estimates(ctx["order"], "2026-09-08T08:30:00")
        self.assertEqual(values[first], 50)  # 100 before start; half of next 100 remains.
        self.assertEqual(values[later], 100)  # Separate later build, never added to first.
        self.assertEqual(remaining_2wp_estimates(ctx["order"], "2026-09-08T08:00:00")[first], 100)
        self.assertEqual(remaining_2wp_estimates(ctx["order"], "2026-09-08T09:00:00")[first], 0)

    def test_estimated_capacity_preserves_whole_payload_overrun_and_retry(self):
        engine = self.engine(context(None))
        result = engine.allocate([payload(1, 60)])[0]
        self.assertEqual((result["assigned_destination"], result["capacity_before_wmt"], result["overrun_wmt"]), ("SP1", 50, 10))
        self.assertEqual(result["capacity_basis"], ESTIMATED_CAPACITY_BASIS)
        self.assertEqual(engine.allocate([payload(1, 60)])[0], result)
        self.assertEqual(engine.allocate([payload(2, 10)])[0]["assigned_destination"], "SP2")

    def test_entered_capacity_and_zero_override_estimate(self):
        for entered in (0, 80):
            engine = self.engine(context(entered))
            first = engine.order["audit"][0]["instance_id"]
            self.assertEqual(engine.instances[first]["starting_wmt"], entered)
            self.assertEqual(engine.instances[first]["capacity_basis"], "User entered")
            self.assertEqual(engine.allocate([payload(1, 10)])[0]["assigned_destination"], "SP2" if entered == 0 else "SP1")

    def test_shared_materials_have_one_estimated_physical_balance(self):
        ctx = context(None, shared=True)
        engine = self.engine(ctx, "2026-09-08T06:30:00")
        # 50 HG plus 100 BA remaining; both consume the same 150 WMT.
        rows = engine.allocate([payload(1, 90), payload(2, 80, material="BA")])
        self.assertEqual([r["capacity_before_wmt"] for r in rows], [150, 60])
        self.assertEqual(rows[1]["overrun_wmt"], 20)

    def test_missing_evidence_and_unconfirmed_build_remain_unresolved(self):
        ctx = context(None)
        for row in ctx["order"]["orders"]:
            row.pop("inbound_windows")
        self.assertEqual(self.engine(ctx).allocate([payload(1)])[0]["status"], "Unresolved")
        ctx = context(None)
        ctx["settings"]["selected_instances"] = {}
        self.assertIn("unconfirmed", self.engine(ctx).allocate([payload(1)])[0]["reason"])

    def test_final_plan_passes_scenario_start_and_keeps_estimate_out_of_settings(self):
        ctx = context(None)
        ctx["start"] = "2026-09-08T08:30:00"
        before = deepcopy(ctx)
        result = allocate_final_plan(transactions([payload(1, 60)]), None, ctx, plan_type="manual")
        self.assertEqual(result["assignments"][0]["capacity_basis"], ESTIMATED_CAPACITY_BASIS)
        self.assertEqual(result["assignments"][0]["capacity_before_wmt"], 50)
        self.assertEqual(ctx, before)
        from tests.test_destination_plan_report import publish
        import pandas as pd
        frames, _ = publish(transactions([payload(1, 60)]), pd.DataFrame(), ctx)
        summary = frames["material_destination_plan"]
        self.assertEqual(summary.iloc[0].capacity_basis, ESTIMATED_CAPACITY_BASIS)
        self.assertEqual(summary.iloc[0].starting_capacity_wmt, 50)
