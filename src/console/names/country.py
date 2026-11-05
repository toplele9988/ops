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


# ISO 3166-1 alpha-2 -> Chinese name. Alibaba buyer IDs ("tm:us2912…") start
# with the buyer account's registration country; it is a hint, not a verified address.
ISO_NAMES = dict(item.split(':') for item in (
    'AD:安道尔 AE:阿联酋 AF:阿富汗 AG:安提瓜和巴布达 AI:安圭拉 AL:阿尔巴尼亚 AM:亚美尼亚 AO:安哥拉 AQ:南极洲 '
    'AR:阿根廷 AS:美属萨摩亚 AT:奥地利 AU:澳大利亚 AW:阿鲁巴 AX:奥兰群岛 AZ:阿塞拜疆 BA:波黑 BB:巴巴多斯 '
    'BD:孟加拉国 BE:比利时 BF:布基纳法索 BG:保加利亚 BH:巴林 BI:布隆迪 BJ:贝宁 BL:圣巴泰勒米 BM:百慕大 '
    'BN:文莱 BO:玻利维亚 BQ:荷兰加勒比区 BR:巴西 BS:巴哈马 BT:不丹 BW:博茨瓦纳 BY:白俄罗斯 BZ:伯利兹 '
    'CA:加拿大 CC:科科斯群岛 CD:刚果（金） CF:中非 CG:刚果（布） CH:瑞士 CI:科特迪瓦 CK:库克群岛 CL:智利 '
    'CM:喀麦隆 CN:中国 CO:哥伦比亚 CR:哥斯达黎加 CU:古巴 CV:佛得角 CW:库拉索 CX:圣诞岛 CY:塞浦路斯 '
    'CZ:捷克 DE:德国 DJ:吉布提 DK:丹麦 DM:多米尼克 DO:多米尼加 DZ:阿尔及利亚 EC:厄瓜多尔 EE:爱沙尼亚 '
    'EG:埃及 EH:西撒哈拉 ER:厄立特里亚 ES:西班牙 ET:埃塞俄比亚 FI:芬兰 FJ:斐济 FK:福克兰群岛 FM:密克罗尼西亚 '
    'FO:法罗群岛 FR:法国 GA:加蓬 GB:英国 GD:格林纳达 GE:格鲁吉亚 GF:法属圭亚那 GG:根西 GH:加纳 '
    'GI:直布罗陀 GL:格陵兰 GM:冈比亚 GN:几内亚 GP:瓜德罗普 GQ:赤道几内亚 GR:希腊 GT:危地马拉 GU:关岛 '
    'GW:几内亚比绍 GY:圭亚那 HK:中国香港 HN:洪都拉斯 HR:克罗地亚 HT:海地 HU:匈牙利 ID:印度尼西亚 IE:爱尔兰 '
    'IL:以色列 IM:马恩岛 IN:印度 IQ:伊拉克 IR:伊朗 IS:冰岛 IT:意大利 JE:泽西 JM:牙买加 JO:约旦 '
    'JP:日本 KE:肯尼亚 KG:吉尔吉斯斯坦 KH:柬埔寨 KI:基里巴斯 KM:科摩罗 KN:圣基茨和尼维斯 KP:朝鲜 KR:韩国 '
    'KW:科威特 KY:开曼群岛 KZ:哈萨克斯坦 LA:老挝 LB:黎巴嫩 LC:圣卢西亚 LI:列支敦士登 LK:斯里兰卡 LR:利比里亚 '
    'LS:莱索托 LT:立陶宛 LU:卢森堡 LV:拉脱维亚 LY:利比亚 MA:摩洛哥 MC:摩纳哥 MD:摩尔多瓦 ME:黑山 '
    'MF:法属圣马丁 MG:马达加斯加 MH:马绍尔群岛 MK:北马其顿 ML:马里 MM:缅甸 MN:蒙古 MO:中国澳门 MP:北马里亚纳群岛 '
    'MQ:马提尼克 MR:毛里塔尼亚 MS:蒙特塞拉特 MT:马耳他 MU:毛里求斯 MV:马尔代夫 MW:马拉维 MX:墨西哥 MY:马来西亚 '
    'MZ:莫桑比克 NA:纳米比亚 NC:新喀里多尼亚 NE:尼日尔 NF:诺福克岛 NG:尼日利亚 NI:尼加拉瓜 NL:荷兰 NO:挪威 '
    'NP:尼泊尔 NR:瑙鲁 NU:纽埃 NZ:新西兰 OM:阿曼 PA:巴拿马 PE:秘鲁 PF:法属波利尼西亚 PG:巴布亚新几内亚 '
    'PH:菲律宾 PK:巴基斯坦 PL:波兰 PM:圣皮埃尔和密克隆 PR:波多黎各 PS:巴勒斯坦 PT:葡萄牙 PW:帕劳 PY:巴拉圭 '
    'QA:卡塔尔 RE:留尼汪 RO:罗马尼亚 RS:塞尔维亚 RU:俄罗斯 RW:卢旺达 SA:沙特阿拉伯 SB:所罗门群岛 SC:塞舌尔 '
    'SD:苏丹 SE:瑞典 SG:新加坡 SI:斯洛文尼亚 SK:斯洛伐克 SL:塞拉利昂 SM:圣马力诺 SN:塞内加尔 SO:索马里 '
    'SR:苏里南 SS:南苏丹 ST:圣多美和普林西比 SV:萨尔瓦多 SX:荷属圣马丁 SY:叙利亚 SZ:斯威士兰 TC:特克斯和凯科斯群岛 '
    'TD:乍得 TG:多哥 TH:泰国 TJ:塔吉克斯坦 TL:东帝汶 TM:土库曼斯坦 TN:突尼斯 TO:汤加 TR:土耳其 '
    'TT:特立尼达和多巴哥 TV:图瓦卢 TW:中国台湾 TZ:坦桑尼亚 UA:乌克兰 UG:乌干达 US:美国 UY:乌拉圭 '
    'UZ:乌兹别克斯坦 VA:梵蒂冈 VC:圣文森特和格林纳丁斯 VE:委内瑞拉 VG:英属维尔京群岛 VI:美属维尔京群岛 VN:越南 '
    'VU:瓦努阿图 WF:瓦利斯和富图纳 WS:萨摩亚 XK:科索沃 YE:也门 YT:马约特 ZA:南非 ZM:赞比亚 ZW:津巴布韦'
).split())
ALIASES = {'UK': 'GB', 'ZR': 'CD'}  # legacy codes still seen in Alibaba IDs


def country_from_alibaba_id(conversation_id):
    """'tm:us2912…' -> ('US', '美国'); anything else -> None."""
    if not isinstance(conversation_id, str):
        return None
    match = re.match(r'tm:([a-z]{2})\d', conversation_id)
    if not match:
        return None
    code = ALIASES.get(match[1].upper(), match[1].upper())
    return (code, ISO_NAMES[code]) if code in ISO_NAMES else None
