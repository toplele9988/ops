import pytest

from console.roles.common import clean_party_name, duration, safe


@pytest.mark.parametrize('age,expected,hours', [
    (0, '刚刚', 0), (3599, '刚刚', 0), (3600, '已等 1 小时', 1),
    (48 * 3600 - 1, '已等 47 小时', 47), (48 * 3600, '已等 2 天', 48),
    (545 * 3600, '已等 22 天', 545),
])
def test_waiting_language_boundaries(age, expected, hours):
    assert duration(2000000 - age, 2000000) == (expected, hours)


def test_unknown_and_future_waiting_times():
    assert duration(None, 2000000) == ('时长待确认', None)
    assert duration(2000001, 2000000) == ('刚刚', 0)


@pytest.mark.parametrize('name,expected', [
    ('虚构星河灯光SUP-0011乐初供应商沟通', '虚构星河灯光'),
    ('虚构星河灯光SUP~乐初供应商沟通', '虚构星河灯光'),
    (' ～虚构快递SUP0001-乐初工作群_ ', '虚构快递'),
    ('虚构 MC -乐初LC工作沟通 CUS0010', '虚构 MC'),
    ('_虚构晨星_cUs：０００２～lEcHu工作对接-', '虚构晨星'),
    ('虚构星河 - LC沟通', '虚构星河'),
    ('SUP0011-乐初工作群', 'SUP0011-乐初工作群'),
    ('虚构 SUP Tools', '虚构 SUP Tools'),
    ('虚构烟机特效-乐初 SUP 工作对接', '虚构烟机特效'),
    ('乐初SUP-虚构之星影视灯工作交流', '乐初-虚构之星影视灯工作交流'),
    ('虚构 SUPER 灯光-乐初工作群', '虚构 SUPER 灯光'),
    ('虚构LC科技', '虚构LC科技'),
    ('虚构 CIRCUS -乐初工作群', '虚构 CIRCUS'),
    ('虚构星河SUP（0011）～乐初工作群', '虚构星河'),
])
def test_company_group_name_cleaning(name, expected):
    assert clean_party_name(name) == expected


@pytest.mark.parametrize('name,expected', [
    ('虚构霁月激光厂家SUP～乐初工作', '虚构霁月激光厂家'),
    ('虚构苍穹灯光SUP-乐初工作对接', '虚构苍穹灯光'),
    ('乐初虚构霜叶沟通工作', '虚构霜叶'),
    ('虚构澄湾～Lechu工作对接', '虚构澄湾'),
    ('FictionalLumen-乐初工作对接', 'FictionalLumen'),
    ('_SUP～乐初工作-虚构霁月工厂—', '虚构霁月工厂'),
    ('沟通工作LC_虚构星桥-CUS', '虚构星桥'),
    ('～虚构星桥_对接群工作Lechu—CUS～', '虚构星桥'),
    (' Lechu-虚构星桥-供应商群沟通 ', '虚构星桥'),
    ('LC工作沟通—虚构星桥SUP', '虚构星桥'),
    ('CUS～００２３—虚构星桥～LC对接供应商工作群', '虚构星桥'),
    ('虚构星桥sUp', '虚构星桥'),
    ('CUS Fictional Willow', 'Fictional Willow'),
    ('虚构星桥-供需', '虚构星桥-供需'),
    ('虚构SUPERNOVA', '虚构SUPERNOVA'),
    ('虚构FOCUS', '虚构FOCUS'),
    ('LCloud虚构科技', 'LCloud虚构科技'),
    ('SUP～乐初工作', 'SUP～乐初工作'),
    ('星SUP～乐初工作', '星SUP～乐初工作'),
    ('乐初A沟通工作CUS', '乐初A沟通工作CUS'),
])
def test_c3_group_labels_at_both_edges_and_short_name_fallback(name, expected):
    assert clean_party_name(name) == expected


@pytest.mark.parametrize('text', [
    '成交简报（2026-10-30）', '2030-01-10 2030-01-11 3 单',
    'USD 1,234.50', '3 单', 'USD 123456789012', '1234567890.50',
    '123456789012 元', '123456789012 单', '订单号：203010300001',
    'Order no: 203010300001',
    '合计123456789012 单', '金额123456789012 元',
])
def test_business_numbers_are_not_contact_details(text):
    assert safe(text) == text


@pytest.mark.parametrize('contact', [
    '+86 138 0013 8000', '13800138000', '+1 (202) 555-0123', '010-55551234',
])
def test_long_phone_numbers_are_hidden_even_next_to_chinese(contact):
    assert safe('虚构电话：' + contact) == '虚构电话：〔号码已隐藏〕'
    assert safe('虚构电话' + contact) == '虚构电话〔号码已隐藏〕'


def test_mixed_business_facts_and_contacts_keep_existing_redactions():
    text = ('2026-10-30 USD 1,234.50 3 单；+86 138 0013 8000；fictional@example.invalid；'
            '/fictional/private；token=fictional-secret ；https://fictional.invalid')
    cleaned = safe(text)
    assert '2026-10-30 USD 1,234.50 3 单' in cleaned
    for hidden in ('138', 'fictional', '/private'):
        assert hidden not in cleaned
    for label in ('号码', '邮箱', '路径', '凭据', '链接'):
        assert f'〔{label}已隐藏〕' in cleaned


@pytest.mark.parametrize('age,expected', [(3599, '刚刚'), (26 * 3600, '已等 26 小时'), (545 * 3600, '已等 22 天')])
def test_waiting_text_is_consistent_across_roles(env, age, expected):
    env.conv(name='虚构新联系人', age=age)
    triage = env.roles.scan_triage('fictional-owner')
    key = env.conv(name='虚构案内客户', age=age)
    did = env.deal(key=key)
    secretary = env.roles.run_secretary(did, 'fictional-owner')
    brief = env.roles.run_brief('fictional-owner')
    texts = [triage['items'][0]['reason'], triage['runs'][0]['output_text'],
             secretary['output_text'], brief['output_json']['waiting_summary'], brief['output_text']]
    for text in texts:
        assert expected in text
        assert '目前由我方确认归属' not in text
        assert '已等刚刚' not in text and '545 小时' not in text and '已等已等' not in text
    assert secretary['output_json']['waiting_on'][0]['waited_hours'] == age // 3600
    if age > 48 * 3600:
        env.inbox.conversations[key]['messages'][0]['role'] = 'seller'
        patrol = env.roles.run_patrol('fictional-owner')
        assert expected in patrol['output_text'] and '545 小时' not in patrol['output_text']


@pytest.mark.parametrize('name,kind', [
    ('虚构烟机特效-乐初 SUP 工作对接', 'supplier'), ('乐初SUP-虚构之星影视灯工作交流', 'supplier'),
    ('虚构 MC -乐初 CUS 工作沟通', 'customer'), ('虚构 SUP Tools', None), ('虚构 SUPER 灯光-乐初工作群', None),
    ('虚构张三 CUS', 'customer'), ('SUP 虚构李四', 'supplier'),
])
def test_boss_sup_cus_tags_anywhere_in_work_group_names(name, kind):
    from console.roles.triage import named_kind
    assert named_kind(name.casefold()) == kind
