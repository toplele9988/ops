"""Read-only archive checks using the archive's current-record semantics.

Only counts, record identifiers and dates leave SQL. Supplier names are streamed
through the existing short-name normalizer inside SQLite, never returned.
"""
import re
import unicodedata

from console.archive.seed import short_name

from .common import finding, read_db, sample_id


# Equivalent to archive.validity.current_offer: confirmed corrections retire
# their direct predecessor, then the latest remaining leaf wins. Suggested or
# rejected corrections never retire a confirmed price, nor does expiry cause a
# fallback to an older price. Hidden products are absent from archive summaries.
CURRENT_OFFERS = """
WITH leaves AS (
  SELECT o.id,o.product_id,o.quoted_on,o.valid_until
  FROM offers o JOIN products p ON p.id=o.product_id
  WHERE o.status='confirmed' AND p.status!='hidden'
    AND NOT EXISTS (SELECT 1 FROM offers newer WHERE newer.product_id=o.product_id
                    AND newer.status='confirmed' AND newer.corrects_offer_id=o.id)
), ranked AS (
  SELECT *,ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY quoted_on DESC,id DESC) AS rank
  FROM leaves
), current_records AS (SELECT id,valid_until FROM ranked WHERE rank=1)
"""

CURRENT_FREIGHT = """
WITH ranked AS (
  SELECT id,valid_until,ROW_NUMBER() OVER (
    PARTITION BY forwarder_id,origin,destination_country,COALESCE(destination_detail,''),
                 mode,service,unit,currency
    ORDER BY quoted_on DESC,id DESC) AS rank
  FROM freight_quotes WHERE status='confirmed'
), current_records AS (SELECT id,valid_until FROM ranked WHERE rank=1)
"""


def _counts(db, query, args=()):
    """Run bounded metadata projection separately from the SQL count."""
    count = db.execute('SELECT COUNT(*) FROM (' + query + ')', args).fetchone()[0]
    rows = db.execute(query + ' ORDER BY id LIMIT 5', args)
    return count, [dict(id=sample_id(row[0])) for row in rows]


def offers_missing_expiry(ctx):
    with read_db(ctx.archive_path) as db:
        query = CURRENT_OFFERS + "SELECT id FROM current_records WHERE NULLIF(trim(valid_until),'') IS NULL"
        count, samples = _counts(db, query)
    return finding('archive.offers_missing_expiry', '供应商报价缺有效期',
        'medium' if count else 'ok', count,
        f'{count} 条当前已确认供应商报价没写有效期，无法判断还能否用于报价。',
        '到 /products 打开产品报价，向供应商确认有效期后补录新报价；不要直接沿用旧价。', samples)


def expired_prices(ctx):
    with read_db(ctx.archive_path) as db:
        counts, samples = {}, []
        for kind, cte in (('offers', CURRENT_OFFERS), ('freight', CURRENT_FREIGHT)):
            query = cte + 'SELECT id FROM current_records WHERE valid_until < ?'
            count, selected = _counts(db, query, (str(ctx.today),))
            counts[kind] = count
            samples.extend(dict(row, kind=kind) for row in selected)
    count = sum(counts.values())
    return finding('archive.expired_prices', '报价和运价已过期', 'high' if count else 'ok', count,
        f'{counts["offers"]} 条当前供应商报价、{counts["freight"]} 条当前运价已过期，继续引用可能报错成本。',
        '到 /products 向供应商更新报价，到 /freight 向货代更新线路运价；到期当天仍有效。',
        samples[:5], counts=counts)


def missing_packing(ctx):
    with read_db(ctx.archive_path) as db:
        query = """SELECT id FROM products WHERE status!='hidden' AND
          (carton_l_cm IS NULL OR carton_w_cm IS NULL OR carton_h_cm IS NULL OR gross_kg IS NULL)"""
        count, samples = _counts(db, query)
    return finding('archive.missing_packing', '产品缺外箱尺寸或毛重', 'medium' if count else 'ok', count,
        f'{count} 个未隐藏产品缺少外箱三边或毛重，运费估算缺少必要依据。',
        '到 /products 打开产品详情，向供应商索取并核对外箱长宽高和毛重，再补入包装字段。', samples)


def _supplier_key(value):
    value = short_name(str(value or ''))
    value = unicodedata.normalize('NFKC', value).casefold()
    # Conservative near-name match: case, width, punctuation and the existing
    # archive city/company/industry suffix rules; never arbitrary edit distance.
    value = re.sub(r'[\W_]+', '', value, flags=re.UNICODE)
    return value if len(value) >= 2 else None


def duplicate_suppliers(ctx):
    with read_db(ctx.archive_path) as db:
        db.create_function('audit_supplier_key', 1, _supplier_key, deterministic=True)
        query = """SELECT MIN(id) AS id,COUNT(*) AS n FROM parties
          WHERE kind='supplier' AND status!='archived'
          GROUP BY audit_supplier_key(name)
          HAVING audit_supplier_key(name) IS NOT NULL AND COUNT(*)>1"""
        groups, count, involved = db.execute(
            'SELECT COUNT(*),COALESCE(SUM(n-1),0),COALESCE(SUM(n),0) FROM (' + query + ')').fetchone()
        samples = [dict(id=sample_id(row[0]), records=row[1])
                   for row in db.execute(query + ' ORDER BY id LIMIT 5')]
    return finding('archive.duplicate_suppliers', '供应商名称疑似重复', 'low' if count else 'ok', count,
        f'{groups} 组供应商名称相同或经简称规则归一后相同，涉及 {involved} 条档案、疑似多建 {count} 条。',
        '到 /suppliers 对照名称、联系方式和来源人工核对，确认同一单位后整理重复档案及关联；不会自动合并。',
        samples, duplicate_groups=groups, involved_records=involved)


def deals_without_conversations(ctx):
    with read_db(ctx.archive_path) as db:
        query = """SELECT d.id FROM deals d WHERE NOT EXISTS (
          SELECT 1 FROM deal_conversations c JOIN deal_participants p
            ON p.deal_id=c.deal_id AND p.party_id=c.party_id
          WHERE c.deal_id=d.id AND NULLIF(trim(c.conversation_key),'') IS NOT NULL)"""
        count, samples = _counts(db, query)
    return finding('archive.deals_without_conversations', '案子未关联参与方会话',
        'medium' if count else 'ok', count,
        f'{count} 个已建案子没有与参与方对应的案内会话，无法据此核对各方沟通和等待状态。',
        '到 /deals 打开案子，补齐参与方，并把该参与方的会话关联到案子；仅有单位档案还不够。', samples)


def pending_triage(ctx):
    with read_db(ctx.archive_path) as db:
        count, samples = _counts(db, "SELECT id FROM triage_suggestions WHERE status='pending'")
    return finding('archive.pending_triage', '会话等待整理确认', 'low' if count else 'ok', count,
        f'{count} 条已扫描会话的分拣建议尚待确认；这里只统计现有队列，未扫描会话不在此数内。',
        '到 /triage 查看建议并确认归属或忽略；需要发现新会话时点击“扫描一遍”。', samples)


def pending_prices(ctx):
    # No extract table/schema exists in either the inspected repository or the
    # read-only archive schema. Absence is an optional feature, not a failure.
    # Do not invent a price-suggestion table/column contract from its name alone.
    with read_db(ctx.archive_path) as db:
        available = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND (name='extract' OR name GLOB 'extract_*') LIMIT 1").fetchone()
    if available is None:
        return None
    raise ValueError('价格抽取表结构未核实')


CHECKS = [
    ('archive.offers_missing_expiry', '供应商报价缺有效期', offers_missing_expiry),
    ('archive.expired_prices', '报价和运价已过期', expired_prices),
    ('archive.missing_packing', '产品缺外箱尺寸或毛重', missing_packing),
    ('archive.duplicate_suppliers', '供应商名称疑似重复', duplicate_suppliers),
    ('archive.deals_without_conversations', '案子未关联参与方会话', deals_without_conversations),
    ('archive.pending_triage', '会话等待整理确认', pending_triage),
    ('archive.pending_prices', '价格建议等待确认', pending_prices),
]
