"""Extendable, non-blocking compliance checks for listing copy."""
import re

DEFAULT_BRAND_TERMS = ()  # Maintained by the owner through Listing(..., brand_terms=...).
_HYPE = re.compile(r'\b(?:best|no\.?\s*1|number\s*one|100\s*%|guaranteed|cheapest|perfect|unbeatable)\b'
                   r'|最(?:好|佳|便宜)|第一(?:名)?|百分之百|保证(?:有效|品质)?|绝对|顶级|无敌', re.I)


def check(draft, *, brand_terms=(), image_count=None, image_dimensions_known=False):
    """Return issues with severity=blocking|warning|info; checks never prevent saving."""
    issues = []
    title = str(draft.get('title') or '')
    keywords = draft.get('keywords') or []
    attrs = draft.get('attributes') or {}
    external = '\n'.join([title, *map(str, keywords), *map(str, draft.get('selling_points') or []),
                          str(draft.get('description') or '')])

    def add(code, severity, message, **extra):
        issues.append(dict(code=code, severity=severity, message=message, **extra))

    if not title.strip() or len(title) > 128:
        add('title_length', 'blocking', '标题须为 1–128 个字符')
    if not 3 <= len(keywords) <= 5:
        add('keyword_count', 'blocking', '关键词须为 3–5 个')
    if _HYPE.search(external):
        add('prohibited_claim', 'blocking', '内容含夸大或绝对化用语')
    terms = tuple(t.strip() for t in brand_terms if isinstance(t, str) and t.strip())
    hits = sorted({term for term in terms if term.casefold() in external.casefold()})
    if hits:
        add('brand_term', 'blocking', '命中待核对商标词表', terms=hits)
    missing = sorted(key for key, value in attrs.items() if not str(value).strip()
                     or 'to be confirmed' in str(value).casefold() or '待补充' in str(value))
    if missing:
        add('missing_attributes', 'warning', '以下属性仍待补充', fields=missing)
    refs = draft.get('image_refs') or []
    count = len(refs) if image_count is None else image_count
    if count < 3:
        add('image_count', 'info', '建议准备至少 3 张清晰产品图；当前仅提示，不处理图片', count=count)
    if count and not image_dimensions_known:
        add('image_resolution', 'info', '尚未检查图片分辨率；请上传清晰、符合平台要求的原图')
    blockers = [issue for issue in issues if issue['severity'] == 'blocking']
    return dict(issues=issues, blocking=blockers,
                warnings=[issue for issue in issues if issue['severity'] == 'warning'],
                ready=not blockers)

