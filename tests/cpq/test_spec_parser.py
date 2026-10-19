import pytest
from console.strobe.spec_parser import KEY_FIELDS, parse_specs


@pytest.mark.parametrize('text,rgb,white', [
    ('768颗RGB：0.5瓦，288颗W：2瓦', 768, 288),
    ('768pcs×0.5W RGB; 288pcs×2W WHITE', 768, 288),
    ('768颗3合1，288颗白光', 768, 288),
    ('768pcs 3in1; 288pcs CW', 768, 288),
    ('768 RGB + 288 white', 768, 288),
    ('288颗WW', None, 288),
])
def test_fengwu_light_source_counts(text, rgb, white):
    result = parse_specs({'light_sources': [text]})
    assert result['values']['rgb_count'] == rgb
    assert result['values']['white_count'] == white
    assert result['values']['power_w'] is None
    assert result['sources']['white_count'] == 'parsed'


def test_chinese_full_spec_can_supply_counts_and_led_type():
    result = parse_specs({'spec_zh_full': '光源：768颗RGB：0.5瓦，288颗W：2瓦；5050 SMD；48段'})
    assert result['values']['rgb_count'] == 768
    assert result['values']['white_count'] == 288
    assert result['values']['led_type'] == '5050 SMD'
    assert result['values']['pixel_segments'] == 48
    assert result['values']['dual'] is True
    assert result['values']['label'] == '768 RGB + 288 W'


def test_rgbw_combined_emitters_do_not_invent_separate_counts():
    result = parse_specs({'light_sources': ['1320pcs×0.8W RGBW']})
    assert result['values']['rgb_count'] is None
    assert result['values']['white_count'] is None
    assert result['values']['led_type'] == 'RGBW'
    assert result['values']['dual'] is None
    assert result['values']['power_w'] is None


def test_ch_prefix_and_suffix_modes():
    result = parse_specs({'spec_zh_full': 'CH6，CH13，CH18；6通道、13ch、18 channels'})
    assert result['values']['dmx_modes'] == [6, 13, 18]
    assert result['sources']['dmx_modes'] == 'parsed'


def test_channel_modes_and_segments_have_priority():
    result = parse_specs({'channel_modes': [12, 8, 12], 'segments': 48,
                          'spec_zh_full': 'CH6，CH13，CH18；24段'})
    assert result['values']['dmx_modes'] == [8, 12]
    assert result['values']['pixel_segments'] == 48
    assert result['sources']['dmx_modes'] == 'column'
    assert result['sources']['pixel_segments'] == 'column'
    assert not result['conflicts']


def test_empty_channel_modes_fall_back_to_text():
    assert parse_specs({'channel_modes': [], 'spec_zh_full': 'CH6'})['values']['dmx_modes'] == [6]


def test_conflicting_counts_and_ip_are_null():
    result = parse_specs({'light_sources': ['768颗RGB'], 'spec_zh_full': '960颗RGB；IP65，IP66'})
    assert result['values']['rgb_count'] is None
    assert result['values']['ip_rating'] is None
    assert {'rgb_count', 'ip_rating'} <= set(result['conflicts'])
    assert result['sources']['rgb_count'] == 'parsed'


def test_repeated_same_evidence_is_not_conflict():
    result = parse_specs({'light_sources': ['768颗RGB'], 'spec_zh_full': '768颗RGB'})
    assert result['values']['rgb_count'] == 768
    assert not result['conflicts']


def test_column_and_override_priority():
    result = parse_specs({'wattage': 1000, 'spec_zh_full': '500W，800W；IP65'},
                         {'power_w': 1200, 'ip_rating': 'IP66'},
                         {'power_w': 1100})
    assert result['values']['power_w'] == 1100
    assert result['sources']['power_w'] == 'override'
    assert result['values']['ip_rating'] == 'IP66'
    assert result['sources']['ip_rating'] == 'column'


def test_nominal_power_uses_maximum_description_value():
    result = parse_specs({'spec_zh_full': '500W / 1000W', 'light_sources': ['1320pcs×0.8W RGBW']})
    assert result['values']['power_w'] == 1000
    assert 'power_w' not in result['conflicts']


def test_conflicting_led_packages_are_null():
    result = parse_specs({'spec_zh_full': '5050 SMD，SMD 3535'})
    assert result['values']['led_type'] is None
    assert 'led_type' in result['conflicts']


def test_unknowns_and_completeness():
    result = parse_specs({})
    assert result['values']['rgb_count'] is None
    assert result['values']['white_count'] is None
    assert result['values']['dmx_modes'] is None
    assert result['values']['net_weight_kg'] is None
    assert result['values']['dimensions_cm'] == {'l': None, 'w': None, 'h': None}
    assert result['values']['carton']['source'] == 'none'
    assert result['spec_completeness'] == {'score': 0.0, 'missing': list(KEY_FIELDS)}
    assert set(result['values']) == set(result['sources'])


def test_batch_does_not_infer_physical_specs_or_certifications():
    result = parse_specs({'spec_zh_full': '520×220×150mm，9.8kg，no CE certification'})
    assert result['values']['net_weight_kg'] is None
    assert result['values']['dimensions_cm'] == {'l': None, 'w': None, 'h': None}
    assert result['values']['certifications'] is None


def test_explicit_certifications_and_protocols():
    result = parse_specs({'certifications': ['CE', 'RoHS'], 'spec_zh_full': 'DMX512 RDM Art-Net sACN Sound Auto Master/Slave'})
    assert result['values']['certifications'] == ['CE', 'RoHS']
    assert result['sources']['certifications'] == 'column'
    assert result['values']['control_protocols'] == ['DMX512', 'RDM', 'Art-Net', 'sACN', 'Sound', 'Auto', 'Master/Slave']


def test_null_override_and_invalid_override():
    cleared = parse_specs({'segments': 48}, overrides={'pixel_segments': None})
    assert cleared['values']['pixel_segments'] is None
    assert cleared['sources']['pixel_segments'] == 'override'
    invalid = parse_specs({}, overrides={'power_w': float('nan')})
    assert invalid['values']['power_w'] is None
    assert 'power_w' in invalid['conflicts']
