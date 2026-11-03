"""Pure classification and access rules; ordered rules are deliberately centralized."""

FAMILIES = {
    'supplier': '供应商资料与报价表', 'technical': '技术参考资料',
    'supplier_chat': '与供应商的沟通', 'customer_conversation': '买家会话',
    'group_chat': '微信群', 'private_note': '私聊笔记/视频转写', 'other': '其他资料',
}
PURPOSES = ('owner', 'product_facts', 'customer_reply')
# First match wins. Customer provenance always precedes shared supplier scopes.
# (family, exact source types, scope roots, source type prefixes, registry roles)
FAMILY_RULES = (
    ('customer_conversation', ('icbu_customer_conversation', 'icbu_pwa_customer_conversation',
        'icbu_conversation_analysis_candidate', 'icbu_official_inquiry',
        'business_customer_inquiry', 'business_customer_attachment', 'whatsapp_business_conversation'),
        ('icbu-tm', 'icbu', 'icbu-analysis', 'icbu-inquiry'), (), ('CHANNEL_ROLE_CUSTOMER',)),
    ('private_note', ('legacy_sealed_business_record',), ('private', 'business:private'), ('owner_',), ()),
    ('supplier_chat', ('business_supplier_message', 'supplier_chat_candidate'),
        ('internal_supplier_chat',), (), ('CHANNEL_ROLE_SUPPLIER',)),
    ('technical', ('public_technical_reference',), ('internal_technical_reference',), (), ()),
    ('supplier', ('supplier', 'supplier_image', 'business_supplier_attachment'),
        ('internal_supplier',), (), ()),
    ('group_chat', ('learning_chat_snapshot', 'learning_qwen_digest_candidate'), ('group',), (), ()),
)

TYPE_LABELS = {
    'supplier': '供应商资料', 'supplier_image': '供应商图片',
    'public_technical_reference': '公开技术参考', 'business_supplier_attachment': '供应商附件',
    'business_supplier_message': '供应商消息', 'supplier_chat_candidate': '供应商沟通整理',
    'business_customer_inquiry': '客户询问', 'business_customer_attachment': '客户附件',
    'icbu_customer_conversation': '阿里买家会话', 'icbu_pwa_customer_conversation': '阿里买家会话',
    'icbu_conversation_analysis_candidate': '阿里会话分析', 'icbu_official_inquiry': '阿里询盘',
    'whatsapp_business_conversation': 'WhatsApp 会话', 'legacy_sealed_business_record': '历史业务记录',
    'learning_chat_snapshot': '学习群记录', 'learning_qwen_digest_candidate': '学习群摘要',
}


def classify(source_type='', source_scope='', channel_role=''):
    for family, types, scopes, prefixes, roles in FAMILY_RULES:
        if (source_type in types or channel_role in roles
                or any(source_scope == s or source_scope.startswith(s + ':') for s in scopes)
                or any(source_type.startswith(p) for p in prefixes)):
            return family
    return 'other'


def type_label(source_type):
    return TYPE_LABELS.get(source_type, '私有笔记与转写' if source_type.startswith('owner_') else '其他资料')


def access_flags(labels, source_type='', family='other'):
    """Unknown explicit labels fail closed. Cost is a sensitivity flag, not a verified price."""
    confidential = family == 'private_note' or source_type == 'legacy_sealed_business_record'
    # No label means nobody has checked it. Supplier quotes and proforma invoices are exactly that kind of
    # file, so unlabeled material stays out of customer replies; only public technical references are open.
    # (A buyer's own conversation has its own gate: the exact scope must match the one being answered.)
    if not labels and family not in ('technical', 'customer_conversation'):
        confidential = True
    cost = False
    for confidentiality, is_cost_price in labels:
        confidential |= confidentiality != 'public'
        cost |= bool(is_cost_price)
    return bool(confidential), cost


def allowed(family, purpose, *, confidential=False, is_cost_price=False,
            source_scope='', conversation_scope=None):
    if purpose not in PURPOSES:
        raise ValueError('用途无效')
    if purpose == 'owner':
        return True
    if purpose == 'product_facts':
        return family in ('supplier', 'technical', 'supplier_chat')
    if confidential or is_cost_price:
        return False
    return family in ('supplier', 'technical') or (
        family == 'customer_conversation' and bool(conversation_scope) and source_scope == conversation_scope)
