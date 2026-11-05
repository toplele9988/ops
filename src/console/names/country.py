"""Small, offline ITU E.164 table. A phone prefix is a location hint only.

Source: https://www.itu.int/dms_pub/itu-t/opb/sp/T-SP-E.164D-2016-PDF-E.pdf
Shared codes +1 and +7 need a recognized national prefix; unknowns stay empty.
"""
import re

# Calling code: (ISO 3166-1 alpha-2, Chinese display name).
COUNTRIES = {
    '20': ('EG', '埃及'), '27': ('ZA', '南非'), '30': ('GR', '希腊'),
    '31': ('NL', '荷兰'), '32': ('BE', '比利时'), '33': ('FR', '法国'),
    '34': ('ES', '西班牙'), '36': ('HU', '匈牙利'), '39': ('IT', '意大利'),
    '40': ('RO', '罗马尼亚'), '41': ('CH', '瑞士'), '43': ('AT', '奥地利'),
    '44': ('GB', '英国'), '45': ('DK', '丹麦'), '46': ('SE', '瑞典'),
    '47': ('NO', '挪威'), '48': ('PL', '波兰'), '49': ('DE', '德国'),
    '51': ('PE', '秘鲁'), '52': ('MX', '墨西哥'), '54': ('AR', '阿根廷'),
    '55': ('BR', '巴西'), '56': ('CL', '智利'), '57': ('CO', '哥伦比亚'),
    '58': ('VE', '委内瑞拉'), '60': ('MY', '马来西亚'), '61': ('AU', '澳大利亚'),
    '62': ('ID', '印度尼西亚'), '63': ('PH', '菲律宾'), '64': ('NZ', '新西兰'),
    '65': ('SG', '新加坡'), '66': ('TH', '泰国'), '81': ('JP', '日本'),
    '82': ('KR', '韩国'), '84': ('VN', '越南'), '86': ('CN', '中国'),
    '90': ('TR', '土耳其'), '91': ('IN', '印度'), '92': ('PK', '巴基斯坦'),
    '93': ('AF', '阿富汗'), '94': ('LK', '斯里兰卡'), '95': ('MM', '缅甸'),
    '98': ('IR', '伊朗'), '212': ('MA', '摩洛哥'), '213': ('DZ', '阿尔及利亚'),
    '216': ('TN', '突尼斯'), '234': ('NG', '尼日利亚'), '254': ('KE', '肯尼亚'),
    '351': ('PT', '葡萄牙'), '353': ('IE', '爱尔兰'), '358': ('FI', '芬兰'),
    '380': ('UA', '乌克兰'), '420': ('CZ', '捷克'), '852': ('HK', '中国香港'),
    '853': ('MO', '中国澳门'), '880': ('BD', '孟加拉国'), '886': ('TW', '中国台湾'),
    '960': ('MV', '马尔代夫'), '961': ('LB', '黎巴嫩'), '962': ('JO', '约旦'),
    '963': ('SY', '叙利亚'), '964': ('IQ', '伊拉克'), '965': ('KW', '科威特'),
    '966': ('SA', '沙特阿拉伯'), '968': ('OM', '阿曼'), '970': ('PS', '巴勒斯坦'),
    '971': ('AE', '阿联酋'), '972': ('IL', '以色列'), '974': ('QA', '卡塔尔'),
    '977': ('NP', '尼泊尔'), '998': ('UZ', '乌兹别克斯坦'),
}

# Deliberately partial shared-code coverage: no default +1 -> US guess.
SHARED = {
    '1202': ('US', '美国'), '1212': ('US', '美国'), '1213': ('US', '美国'),
    '1310': ('US', '美国'), '1415': ('US', '美国'), '1650': ('US', '美国'),
    '1214': ('US', '美国'), '1305': ('US', '美国'), '1312': ('US', '美国'),
    '1617': ('US', '美国'), '1206': ('US', '美国'), '1718': ('US', '美国'),
    '1416': ('CA', '加拿大'), '1647': ('CA', '加拿大'), '1437': ('CA', '加拿大'),
    '1604': ('CA', '加拿大'), '1778': ('CA', '加拿大'), '1514': ('CA', '加拿大'),
    '1438': ('CA', '加拿大'), '1613': ('CA', '加拿大'), '1403': ('CA', '加拿大'),
    '1587': ('CA', '加拿大'), '1780': ('CA', '加拿大'), '1905': ('CA', '加拿大'),
    '1809': ('DO', '多米尼加'), '1829': ('DO', '多米尼加'), '1849': ('DO', '多米尼加'),
    '1876': ('JM', '牙买加'), '1658': ('JM', '牙买加'), '1242': ('BS', '巴哈马'),
    '1787': ('PR', '波多黎各'), '1939': ('PR', '波多黎各'),
    '76': ('KZ', '哈萨克斯坦'), '77': ('KZ', '哈萨克斯坦'),
    '73': ('RU', '俄罗斯'), '74': ('RU', '俄罗斯'), '78': ('RU', '俄罗斯'),
    '79': ('RU', '俄罗斯'),
}


def phone_digits(number):
    if not isinstance(number, str) or len(number) > 64:
        return None
    number = number.strip()
    if not re.fullmatch(r'\+?[0-9 ()\-]+', number):
        return None
    digits = re.sub(r'[ ()\-]', '', number).removeprefix('+')
    if digits.startswith('00'):
        digits = digits[2:]
    return digits if re.fullmatch(r'[1-9][0-9]{7,14}', digits) else None


def country_from_phone(number):
    """Return (ISO code, Chinese name), or None for unknown/ambiguous prefixes."""
    digits = phone_digits(number)
    if not digits:
        return None
    table = SHARED if digits.startswith(('1', '7')) else COUNTRIES
    for prefix in sorted(table, key=len, reverse=True):
        if digits.startswith(prefix):
            return table[prefix]
    return None


def phone_label(number):
    digits = phone_digits(number)
    if not digits:
        return None
    code = next((digits[:n] for n in (1, 2, 3)
                 if digits[:n] in COUNTRIES or digits[:n] in ('1', '7')), None)
    country = country_from_phone(number)
    label = '+' + code + ' ' + digits[len(code):] if code else '+' + digits
    return label + ('（' + country[1] + '）' if country else '')
