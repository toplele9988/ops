import unittest

from console.supplier_data import extract_packing_facts


class PackingExtractorTests(unittest.TestCase):
    def values(self, text):
        return {fact.kind: fact.value for fact in extract_packing_facts(text)}

    def test_chinese_per_axis_mm_and_weights(self):
        self.assertEqual(self.values('净重：11.8kg 毛重：12.8kg 包装尺寸：600mm*350mm*335mm'), {
            'net_weight_kg': 11.8, 'gross_weight_kg': 12.8,
            'carton_size': (60, 35, 33.5),
        })

    def test_english_abbreviations_are_case_insensitive(self):
        self.assertEqual(self.values('Size:413*235*235mm N.W.: 7.5kg G.W.: 8.1KG'), {
            'product_size': (41.3, 23.5, 23.5), 'net_weight_kg': 7.5,
            'gross_weight_kg': 8.1,
        })

    def test_product_carton_and_flightcase_do_not_mix(self):
        text = ('型号 VIRTUAL-A 产品尺寸：27x27x39cm；型号 VIRTUAL-B '
                '外箱尺寸：56.5x56.5x42cm；航空箱尺寸：1.1x0.5x0.8m '
                '航空箱自重：12000g 净重：4.3kg 毛重：69kg')
        self.assertEqual(self.values(text), {
            'product_size': (27, 27, 39), 'carton_size': (56.5, 56.5, 42),
            'flightcase_size': (110, 50, 80), 'flightcase_weight_kg': 12,
            'net_weight_kg': 4.3, 'gross_weight_kg': 69,
        })

    def test_generic_chinese_size_is_only_product_size(self):
        self.assertEqual(self.values('尺寸：27x27x39cm'), {'product_size': (27, 27, 39)})

    def test_english_long_labels(self):
        self.assertEqual(self.values('Product dimensions:20x30x40cm '
                                     'Carton Size:40x50x60cm Flight Case Size:80x90x100cm '
                                     'Net Weight:2000g Gross Weight:4500g Flight case tare weight:1kg'), {
            'product_size': (20, 30, 40), 'carton_size': (40, 50, 60),
            'flightcase_size': (80, 90, 100), 'net_weight_kg': 2,
            'gross_weight_kg': 4.5, 'flightcase_weight_kg': 1,
        })

    def test_fullwidth_original_text_and_chunk_id_are_preserved(self):
        text = '包装尺寸：６００ｍｍ＊３５０ｍｍ＊３３５ｍｍ 毛重：８１００ｇ'
        facts = extract_packing_facts(text, chunk_id='virtual-chunk-1')
        self.assertEqual(facts[0].raw, '包装尺寸：６００ｍｍ＊３５０ｍｍ＊３３５ｍｍ')
        self.assertEqual(facts[1].raw, '毛重：８１００ｇ')
        self.assertTrue(all(fact.chunk_id == 'virtual-chunk-1' for fact in facts))
        self.assertTrue(all(not fact.unit_inferred and fact.confidence == 'labeled' for fact in facts))

    def test_separators(self):
        for separator in ('×', 'x', 'X', '*', '＊', '乘'):
            with self.subTest(separator=separator):
                self.assertEqual(self.values(f'纸箱尺寸 260 {separator} 270 {separator} 350(mm)'),
                                 {'carton_size': (26, 27, 35)})

    def test_label_and_trailing_unit_hints(self):
        for text in ('纸箱尺寸(mm)：260x270x350', '纸箱尺寸：260x270x350（mm）',
                     '纸箱尺寸：[mm] 260x270x350'):
            with self.subTest(text=text):
                facts = extract_packing_facts(text)
                self.assertEqual(facts[0].value, (26, 27, 35))
                self.assertFalse(facts[0].unit_inferred)

    def test_missing_units_mm_inference_is_explicit(self):
        fact = extract_packing_facts('外箱尺寸 600x350x335')[0]
        self.assertEqual(fact.value, (60, 35, 33.5))
        self.assertTrue(fact.unit_inferred)
        self.assertEqual(fact.confidence, 'inferred')

    def test_missing_units_flightcase_cm_inference(self):
        facts = extract_packing_facts('航包尺寸：111.5x33.5x85（长宽高） 净重：43KG 毛重：69KG')
        self.assertEqual(facts[0].kind, 'flightcase_size')
        self.assertEqual(facts[0].value, (111.5, 33.5, 85))
        self.assertTrue(facts[0].unit_inferred)
        self.assertEqual([fact.value for fact in facts[1:]], [43, 69])

    def test_mixed_explicit_units(self):
        self.assertEqual(self.values('包装尺寸:0.5m x 500mm x 60cm'),
                         {'carton_size': (50, 50, 60)})

    def test_carton_count_labels_and_unit_phrases(self):
        for text in ('装箱数：4pcs/箱', '装箱数量：4', '每箱数量：4台', '4台/箱',
                     '4pcs/carton', 'units per carton:4', 'QTY/CTN:4'):
            with self.subTest(text=text):
                self.assertEqual(self.values(text), {'units_per_carton': 4})

    def test_explicit_single_unit_per_carton(self):
        self.assertEqual(self.values('1台/箱'), {'units_per_carton': 1})

    def test_bare_numbers_and_bare_weights_are_ignored(self):
        facts = extract_packing_facts('600x350x335mm 20×30×40 12.8kg 99 4pcs 功率:600W')
        self.assertEqual(facts, [])

    def test_english_label_substrings_inside_words_are_ignored(self):
        self.assertEqual(extract_packing_facts('oversize20x30x40cm internet8kg repacking20x30x40cm'), [])

    def test_missing_weight_units_are_ignored(self):
        self.assertEqual(extract_packing_facts('净重:7.5 毛重:8.1'), [])

    def test_grams_normalize_to_kg(self):
        self.assertEqual(self.values('净重:7500g 毛重:8100G 航空箱重量:15000g'),
                         {'net_weight_kg': 7.5, 'gross_weight_kg': 8.1, 'flightcase_weight_kg': 15})

    def test_out_of_range_values_are_discarded_and_counted(self):
        facts = extract_packing_facts('净重:49g 毛重:501kg 外箱尺寸:1x30x40cm '
                                     '航空箱尺寸:301x30x40cm 装箱数:0 装箱数:1.5 毛重:-2kg')
        self.assertEqual(facts, [])
        self.assertEqual(facts.dropped_out_of_range, 7)
        self.assertEqual(extract_packing_facts('').dropped_out_of_range, 0)

    def test_ranges_are_inclusive(self):
        self.assertEqual(self.values('净重:50g 毛重:500kg 外箱尺寸:2x300x2cm'),
                         {'net_weight_kg': 0.05, 'gross_weight_kg': 500, 'carton_size': (2, 300, 2)})

    def test_ambiguous_unit_hints_are_discarded(self):
        facts = extract_packing_facts('外箱尺寸(mm):20x30x40(cm) 外箱尺寸:20cm x 30 x 40')
        self.assertEqual(facts, [])
        self.assertEqual(facts.dropped_ambiguous, 2)

    def test_duplicate_facts_remain_separate_sources(self):
        facts = extract_packing_facts('毛重:10kg 毛重:12kg')
        self.assertEqual([fact.value for fact in facts], [10, 12])
        self.assertEqual([fact.raw for fact in facts], ['毛重:10kg', '毛重:12kg'])

    def test_unsupported_units_are_not_partially_parsed_as_unitless(self):
        for text in ('外箱尺寸:260x270x350in', '外箱尺寸:20x30x40 inches',
                     '外箱尺寸:20x30x40厘米'):
            with self.subTest(text=text):
                self.assertEqual(extract_packing_facts(text), [])

    def test_ranges_and_extra_axes_are_not_silently_truncated(self):
        for text in ('外箱尺寸:20x30x40x50cm', '外箱尺寸:20x30x40-50cm',
                     '装箱数:2-4台/箱', '装箱数:2或4台/箱', '装箱数:4pcs/pallet'):
            with self.subTest(text=text):
                self.assertEqual(extract_packing_facts(text), [])


if __name__ == '__main__':
    unittest.main()
