import unittest

from console.supplier_data import (aggregate_facts, build_followups, chargeable_weight_kg,
                                   extract_packing_facts, volumetric_weight_kg)
from console.supplier_data.followup import MISSING_PREVIEW_LIMIT


def product(supplier, model, text):
    return {'equipment_id': 'virtual-' + model, 'supplier': supplier, 'model': model,
            'name': '虚构灯具', 'facts': aggregate_facts(extract_packing_facts(text))}


class FollowupTests(unittest.TestCase):
    def test_supplier_grouping_and_missing_fields(self):
        draft = build_followups([product('虚构甲厂', 'A-1', ''),
                                 product('虚构乙厂', 'B-2', '毛重:8kg'),
                                 product('虚构甲厂', 'A-3', '')])
        self.assertEqual(draft.count('供应商：虚构甲厂'), 1)
        self.assertIn('供应商：虚构乙厂', draft)
        for value in ('A-1', 'B-2', 'A-3', '外箱或航空箱尺寸', '毛重', '每箱数量', '尚未发送'):
            self.assertIn(value, draft)

    def test_conflict_includes_both_weights_and_dimensions(self):
        draft = build_followups([product('虚构甲厂', 'A-1',
            '毛重:10kg 毛重:12kg 外箱尺寸:60x35x30cm 外箱尺寸:60x35x39cm')])
        for value in ('请确认毛重', '10 kg', '12 kg', '60 × 35 × 30 cm', '60 × 35 × 39 cm', '每箱数量'):
            self.assertIn(value, draft)
        self.assertIn('版本/批次', draft)

    def test_multi_model_draft_requests_separate_models_and_never_picks(self):
        draft = build_followups([product('虚构甲厂', 'MIXED',
            '毛重:1.5kg 毛重:19kg 产品尺寸:21x18x19cm 产品尺寸:57x44x42cm')])
        for value in ('请按型号分别提供', '1.5 kg', '19 kg', '所属型号及包装方案'):
            self.assertIn(value, draft)
        self.assertNotIn('正确值', draft)

    def test_estimable_still_asks_for_count_and_warns(self):
        draft = build_followups([product('虚构甲厂', 'ESTIMATE', '外箱尺寸:60x35x30cm 毛重:12kg')])
        self.assertIn('待确认每箱数量：请确认包装是否单台一箱，如不是请告知每箱数量', draft)
        self.assertIn('按每箱 1 台估算，须向供应商确认', draft)

    def test_empty_products_merge_per_supplier_with_limited_examples(self):
        draft = build_followups([product('虚构甲厂', f'EMPTY-{index:04}', '') for index in range(2000)]
                                + [product('虚构乙厂', 'OTHER', '')])
        self.assertIn('以下 2000 条资料暂无任何包装信息', draft)
        self.assertIn('以下 1 条资料暂无任何包装信息', draft)
        self.assertIn(f'EMPTY-{MISSING_PREVIEW_LIMIT - 1:04}', draft)
        self.assertNotIn(f'EMPTY-{MISSING_PREVIEW_LIMIT:04}', draft)
        self.assertNotIn('EMPTY-1999', draft)
        self.assertIn('其余省略', draft)
        self.assertLess(len(draft.splitlines()), 25)

    def test_conflict_includes_all_more_than_two_candidates(self):
        draft = build_followups([product('虚构甲厂', 'A-1', '毛重:10kg 毛重:12kg 毛重:15kg')])
        for value in ('10 kg', '12 kg', '15 kg'):
            self.assertIn(value, draft)

    def test_ready_products_are_excluded(self):
        self.assertEqual(build_followups([product('虚构甲厂', 'READY',
            '外箱尺寸:60x35x30cm 毛重:12kg 1台/箱')]), '')


class VolumetricTests(unittest.TestCase):
    def test_default_and_explicit_divisor(self):
        self.assertEqual(volumetric_weight_kg(60, 40, 50), 24)
        self.assertEqual(volumetric_weight_kg(60, 40, 50, divisor=6000), 20)

    def test_chargeable_uses_larger_weight(self):
        self.assertEqual(chargeable_weight_kg(20, 24), 24)
        self.assertEqual(chargeable_weight_kg(30, 24), 30)
        self.assertEqual(chargeable_weight_kg(0, 0), 0)

    def test_invalid_inputs(self):
        for args in ((0, 1, 1), (-1, 1, 1), (float('nan'), 1, 1),
                     (1, 1, 1, 0), (1, 1, 1, float('inf')), ('60', 40, 50), (True, 1, 1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                volumetric_weight_kg(*args)
        for args in ((-1, 1), (1, float('nan')), (float('inf'), 1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                chargeable_weight_kg(*args)


if __name__ == '__main__':
    unittest.main()
