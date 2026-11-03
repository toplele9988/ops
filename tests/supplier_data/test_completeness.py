import unittest

from console.supplier_data import aggregate_facts, assess, extract_packing_facts
from console.supplier_data.completeness import SHIPPING_SIZE_LABEL, SINGLE_UNIT_ASSUMPTION


READY_TEXT = '外箱尺寸:60x35x33.5cm 毛重:12.8kg 1台/箱'


class CompletenessTests(unittest.TestCase):
    def assessment(self, text):
        return assess(aggregate_facts(extract_packing_facts(text)))

    def test_no_evidence_is_missing_and_zero(self):
        result = self.assessment('型号VIRTUAL-1 功率100W')
        self.assertEqual(result.status, 'missing')
        self.assertEqual(result.missing, [SHIPPING_SIZE_LABEL, '毛重', '每箱数量'])
        self.assertEqual(result.optional_missing, ['净重', '产品尺寸'])
        self.assertEqual(result.score, 0)
        self.assertFalse(result.ddp_ready)
        self.assertFalse(result.ddp_estimable)
        self.assertEqual(result.assumptions, [])

    def test_only_product_size_cannot_supply_shipping_dimensions(self):
        result = self.assessment('Size:60x35x33.5cm 毛重:12.8kg 1台/箱')
        self.assertEqual(result.status, 'partial')
        self.assertIn(SHIPPING_SIZE_LABEL, result.missing)
        self.assertFalse(result.ddp_ready)

    def test_required_only_is_ready_at_80_points(self):
        result = self.assessment(READY_TEXT)
        self.assertEqual(result.status, 'ready')
        self.assertTrue(result.ddp_ready)
        self.assertFalse(result.ddp_estimable)
        self.assertEqual(result.assumptions, [])
        self.assertEqual(result.missing, [])
        self.assertEqual(result.score, 80)
        self.assertEqual(result.optional_missing, ['净重', '产品尺寸'])

    def test_complete_is_100_points(self):
        result = self.assessment(READY_TEXT + ' 净重:11.8kg 产品尺寸:50x30x30cm')
        self.assertEqual(result.score, 100)
        self.assertEqual(result.optional_missing, [])

    def test_flightcase_is_valid_shipping_size(self):
        result = self.assessment('航空箱尺寸:110x50x80cm 毛重:69kg 装箱数:4')
        self.assertTrue(result.ddp_ready)

    def test_missing_count_is_estimable_with_explicit_warning_only(self):
        facts = aggregate_facts(extract_packing_facts('外箱尺寸:60x35x33.5cm 毛重:12.8kg'))
        result = assess(facts)
        self.assertEqual(result.missing, ['每箱数量'])
        self.assertFalse(result.ddp_ready)
        self.assertTrue(result.ddp_estimable)
        self.assertEqual(result.status, 'estimable')
        self.assertEqual(result.score, 60)
        self.assertEqual(result.assumptions, [SINGLE_UNIT_ASSUMPTION])
        self.assertNotIn('units_per_carton', facts)

    def test_estimable_boundary_requires_shipping_size_and_gross(self):
        for text in ('外箱尺寸:60x35x30cm', '毛重:12kg',
                     '产品尺寸:60x35x30cm 毛重:12kg',
                     '航空箱尺寸:110x50x80cm 航空箱自重:12kg'):
            with self.subTest(text=text):
                result = self.assessment(text)
                self.assertEqual(result.status, 'partial')
                self.assertFalse(result.ddp_ready)
                self.assertFalse(result.ddp_estimable)
                self.assertEqual(result.assumptions, [])
        self.assertTrue(self.assessment('航空箱尺寸:110x50x80cm 毛重:12kg').ddp_estimable)

    def test_eighty_points_without_count_is_still_only_estimable(self):
        result = self.assessment('外箱尺寸:60x35x30cm 毛重:12kg 净重:10kg 产品尺寸:50x30x25cm')
        self.assertEqual(result.score, 80)
        self.assertTrue(result.ddp_estimable)
        self.assertFalse(result.ddp_ready)

    def test_case_tare_cannot_supply_gross_weight(self):
        result = self.assessment('航空箱尺寸:110x50x80cm 航空箱自重:12kg 装箱数:4')
        self.assertIn('毛重', result.missing)
        self.assertFalse(result.ddp_ready)

    def test_conflict_retains_all_candidates_and_source_ids(self):
        left = extract_packing_facts('毛重:10kg', chunk_id='virtual-old')
        right = extract_packing_facts('毛重:12kg', chunk_id='virtual-new')
        group = aggregate_facts(left + right)['gross_weight_kg']
        self.assertTrue(group.conflict)
        self.assertEqual(group.conflict_reason, 'value_mismatch')
        self.assertEqual([fact.value for fact in group.candidates], [10, 12])
        self.assertEqual([fact.chunk_id for fact in group.candidates], ['virtual-old', 'virtual-new'])

    def test_tolerance_boundary(self):
        for other, expected in ((10.1, None), (10.2, None), (10.201, 'value_mismatch'),
                                (13, 'value_mismatch'), (13.001, 'multi_model_suspected')):
            with self.subTest(other=other):
                group = aggregate_facts(extract_packing_facts(f'毛重:10kg 毛重:{other}kg'))
                self.assertEqual(group['gross_weight_kg'].conflict, expected is not None)
                self.assertEqual(group['gross_weight_kg'].conflict_reason, expected)

    def test_dimension_reason_boundaries_each_axis_and_both_orders(self):
        for axis in range(3):
            for other, reason in ((20.4, None), (20.401, 'value_mismatch'),
                                  (26, 'value_mismatch'), (26.001, 'multi_model_suspected')):
                value = [20, 20, 20]
                value[axis] = other
                changed = 'x'.join(map(str, value))
                for left, right in (('20x20x20', changed), (changed, '20x20x20')):
                    with self.subTest(axis=axis, left=left, right=right):
                        group = aggregate_facts(extract_packing_facts(
                            f'产品尺寸:{left}cm 产品尺寸:{right}cm'))['product_size']
                        self.assertEqual(group.conflict_reason, reason)

    def test_count_conflicts_use_same_numeric_thresholds(self):
        for other, reason in ((102, None), (103, 'value_mismatch'),
                              (130, 'value_mismatch'), (131, 'multi_model_suspected')):
            with self.subTest(other=other):
                group = aggregate_facts(extract_packing_facts(
                    f'装箱数:100 装箱数:{other}'))['units_per_carton']
                self.assertEqual(group.conflict_reason, reason)

    def test_severe_pair_dominates_regardless_of_order_and_retains_candidates(self):
        for values in ('10 12 19', '19 12 10', '12 10 19'):
            group = aggregate_facts(extract_packing_facts(
                ' '.join(f'毛重:{value}kg' for value in values.split())))['gross_weight_kg']
            self.assertEqual(group.conflict_reason, 'multi_model_suspected')
            self.assertEqual(len(group.candidates), 3)

    def test_any_conflict_blocks_both_tiers_without_assumptions(self):
        for count in ('', ' 1台/箱'):
            for conflict in (' 毛重:13.1kg', ' 净重:10kg 净重:10.3kg',
                             ' 产品尺寸:21x18x19cm 产品尺寸:57x44x42cm'):
                with self.subTest(count=count, conflict=conflict):
                    result = self.assessment('外箱尺寸:60x35x30cm 毛重:10kg' + count + conflict)
                    self.assertEqual(result.status, 'conflict')
                    self.assertFalse(result.ddp_ready)
                    self.assertFalse(result.ddp_estimable)
                    self.assertEqual(result.assumptions, [])

    def test_product_reason_prioritizes_multi_model_over_other_mismatches(self):
        result = self.assessment('毛重:10kg 毛重:12kg 产品尺寸:21x18x19cm 产品尺寸:57x44x42cm')
        self.assertEqual(result.conflict_reason, 'multi_model_suspected')

    def test_pairwise_comparison_does_not_hide_distant_endpoints(self):
        group = aggregate_facts(extract_packing_facts('毛重:10.2kg 毛重:10kg 毛重:10.4kg'))
        self.assertTrue(group['gross_weight_kg'].conflict)

    def test_dimension_conflict_checks_each_ordered_axis(self):
        group = aggregate_facts(extract_packing_facts('外箱尺寸:60x35x30cm 外箱尺寸:60x35x33cm'))
        self.assertTrue(group['carton_size'].conflict)
        self.assertEqual(len(group['carton_size'].candidates), 2)

    def test_carton_and_flightcase_sizes_are_separate_kinds(self):
        group = aggregate_facts(extract_packing_facts('外箱尺寸:60x35x30cm 航空箱尺寸:110x50x80cm'))
        self.assertFalse(group['carton_size'].conflict)
        self.assertFalse(group['flightcase_size'].conflict)

    def test_gross_conflict_blocks_ready_and_removes_score(self):
        result = self.assessment(READY_TEXT + ' 毛重:15kg')
        self.assertEqual(result.status, 'conflict')
        self.assertFalse(result.ddp_ready)
        self.assertEqual(result.score, 50)
        self.assertEqual(result.conflicts, ['gross_weight_kg'])
        self.assertEqual(result.missing, [])

    def test_optional_conflict_also_blocks_ready(self):
        result = self.assessment(READY_TEXT + ' 净重:8kg 净重:10kg')
        self.assertEqual(result.status, 'conflict')
        self.assertFalse(result.ddp_ready)

    def test_conflict_and_missing_can_coexist(self):
        result = self.assessment('毛重:8kg 毛重:10kg')
        self.assertEqual(result.status, 'conflict')
        self.assertIn('每箱数量', result.missing)
        self.assertFalse(result.ddp_ready)


if __name__ == '__main__':
    unittest.main()
