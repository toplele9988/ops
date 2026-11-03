"""Compact offline view: text-only DOM updates and at most 50 product rows.

The embedded HTML payload is private to this renderer. Public evidence and
source contracts live in packing_report.json; HTML omits full source chunks.
"""
from html import escape
import json

from .completeness import CONFLICT_REASON_LABELS, KIND_LABELS


STATUS_LABELS = {'ready': '可直接报价（资料齐全）',
                 'estimable': '可估算（须向供应商确认装箱数）',
                 'partial': '资料不全', 'missing': '资料不全', 'conflict': '数值有冲突'}
PAGE_SIZE = 50


def _e(value):
    return escape(str(value), quote=True)


def _html_data(report):
    """Intern repeated source strings; retain all products for explicit filters.

    Row: supplier index, label string index, status, score, missing indices,
    optional indices, assumption indices, reason, groups, dropped, ambiguous,
    source file index, equipment ID index, source type index.
    Group: kind, reason, candidates. Candidate: value, raw index, chunk index,
    inferred, confidence. This is a view encoding, not the public JSON schema.
    """
    strings, string_ids = [], {}

    def intern(value):
        value = str(value)
        if value not in string_ids:
            string_ids[value] = len(strings)
            strings.append(value)
        return string_ids[value]

    suppliers = [entry['supplier'] for entry in report['supplier_coverage']]
    supplier_ids = {supplier: index for index, supplier in enumerate(suppliers)}
    drafts = [[intern(line) for line in report['followups'][supplier].splitlines()]
              for supplier in suppliers]
    rows = []
    for product in report['products']:
        comp = product['completeness']
        groups = []
        for kind, group in product['facts'].items():
            groups.append([kind, group['conflict_reason'], [
                [fact['value'], intern(fact['raw']), intern(fact['chunk_id']),
                 fact['unit_inferred'], fact['confidence']]
                for fact in group['candidates']]])
        rows.append([supplier_ids[product['supplier']], intern(product['label']),
                     comp['status'], comp['score'],
                     [intern(label) for label in comp['missing']],
                     [intern(label) for label in comp['optional_missing']],
                     [intern(label) for label in comp['assumptions']], comp['conflict_reason'],
                     groups, product['dropped_out_of_range'], product['dropped_ambiguous'],
                     intern(product.get('source_file', '')), intern(product['equipment_id']),
                     intern(product.get('source_type', ''))])
    return {'page_size': PAGE_SIZE, 'default_filter': 'action', 'strings': strings,
            'suppliers': suppliers, 'drafts': drafts, 'rows': rows, 'statuses': STATUS_LABELS,
            'kinds': KIND_LABELS, 'reasons': CONFLICT_REASON_LABELS}


CSS = '''
:root{color-scheme:light dark;--bg:#f4f6fa;--panel:#fff;--ink:#18212f;--muted:#536177;--border:#d7dfe9;--accent:#245fc8;--ready:#176342;--estimable:#805000;--partial:#805000;--missing:#536177;--conflict:#a02434}
@media(prefers-color-scheme:dark){:root{--bg:#111720;--panel:#1a2330;--ink:#edf2fa;--muted:#b0bfd3;--border:#3b4c62;--accent:#89b4ff;--ready:#7ce0b4;--estimable:#ffcc76;--partial:#ffcc76;--missing:#b0bfd3;--conflict:#ff9da8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,-apple-system,"Microsoft YaHei",sans-serif}main{max-width:1500px;margin:auto;padding:28px}h1{font-size:clamp(25px,4vw,36px);margin:0}h2{font-size:22px;overflow-wrap:anywhere}p{margin:8px 0}.muted,small{color:var(--muted)}.notice{padding:12px 16px;border-inline-start:4px solid var(--partial);background:var(--panel);border-radius:6px;margin:20px 0}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.card,.supplier{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:18px}.card strong{font-size:36px;display:block;line-height:1.4}.card small{display:block}.bar{height:7px;background:var(--border);border-radius:10px;margin:10px 0;overflow:hidden}.bar span{display:block;height:100%;background:var(--accent)}.controls,.pagination{display:flex;gap:16px;flex-wrap:wrap;align-items:center;margin:24px 0 10px}label{display:flex;align-items:center;gap:8px}input,select,button,textarea{font:inherit;background:var(--panel);color:var(--ink);border:1px solid var(--border);border-radius:6px;padding:8px 12px;max-width:100%}button{cursor:pointer;border-color:var(--accent);color:var(--accent)}button:disabled{opacity:.55;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px}.supplier{margin:16px 0}.supplier-head{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}.supplier h3{margin:0;overflow-wrap:anywhere}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px;table-layout:fixed}th,td{text-align:left;vertical-align:top;padding:12px 10px;border-bottom:1px solid var(--border);overflow-wrap:anywhere}th{color:var(--muted)}.products th:nth-child(1){width:22%}.products th:nth-child(2){width:27%}.products th:nth-child(3){width:10%}.products th:nth-child(4){width:13%}.products th:nth-child(5){width:28%}.status{display:inline-block;font-weight:650;border:1px solid currentColor;border-radius:6px;padding:2px 8px}.ready{color:var(--ready)}.estimable{color:var(--estimable)}.partial{color:var(--partial)}.missing{color:var(--missing)}.conflict{color:var(--conflict)}details{margin:4px 0 8px}summary{cursor:pointer}blockquote{white-space:pre-wrap;overflow-wrap:anywhere;border-inline-start:3px solid var(--border);padding:6px 12px;margin:10px 0}.source{padding:8px 0;border-bottom:1px dashed var(--border)}textarea{display:block;width:100%;min-height:190px;resize:vertical;white-space:pre-wrap}.draft{margin-top:18px}.copy-status{font-size:14px;color:var(--muted)}[hidden]{display:none!important}.empty{padding:20px}.foot{margin-top:24px}noscript{display:block;color:var(--partial)}
@media(max-width:850px){main{padding:16px}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.card{padding:12px}thead{display:none}table,tbody,tr,td{display:block;width:100%}tr{padding:12px 0;border-bottom:2px solid var(--border)}td{border:0;padding:6px 0}td::before{content:attr(data-label);display:block;font-size:12px;color:var(--muted);margin-bottom:3px}.controls,label,input,select{width:100%}.table-wrap{overflow:visible}.pagination{gap:8px}}
'''


JS = '''
// Bar widths are set here so the markup carries no inline style attributes (strict CSP hosts).
document.querySelectorAll('[data-w]').forEach(el => { el.style.width = el.dataset.w + '%'; });
const data = JSON.parse(document.getElementById('report-data').content.textContent);
const search = document.getElementById('search');
const statusFilter = document.getElementById('status-filter');
const supplierFilter = document.getElementById('supplier-filter');
const body = document.getElementById('product-rows');
const previous = document.getElementById('previous');
const next = document.getElementById('next');
data.drafts.forEach((lines, index) => {
  const area = document.getElementById('draft-' + index);
  if (area) area.value = lines.map(i => data.strings[i]).join('\\n');
});
let page = 1;
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function formatValue(kind, value) {
  if (Array.isArray(value)) return value.join(' × ') + ' 厘米';
  return value + (kind === 'units_per_carton' ? ' 台/箱' : ' 千克');
}
function matches(row, query, status, supplier) {
  const state = row[2];
  const statusMatch = status === 'action' ? ['partial', 'estimable', 'conflict'].includes(state)
    : status === 'all' || state === status || status === 'incomplete' && ['partial', 'missing'].includes(state) ||
      (status === 'multi_model_suspected' || status === 'value_mismatch') && row[7] === status;
  return statusMatch && (supplier === '' || row[0] === Number(supplier)) &&
    (!query || (data.suppliers[row[0]] + ' ' + data.strings[row[1]] + ' ' +
      data.strings[row[11]]).toLocaleLowerCase().includes(query));
}
function shorten(value) { return value.length > 24 ? value.slice(0, 18) + '…' + value.slice(-4) : value; }
async function copyId(value, feedback, parent) {
  try {
    if (!navigator.clipboard || !window.isSecureContext) throw new Error('fallback');
    await navigator.clipboard.writeText(value);
    feedback.textContent = '编号已复制';
  } catch (_) {
    const area = node('textarea');
    area.value = value;
    area.readOnly = true;
    parent.append(area);
    area.focus();
    area.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch (_) {}
    feedback.textContent = copied ? '编号已复制' : '已选中完整编号，请使用系统复制操作';
    if (copied) area.remove();
  }
}
function renderRow(row) {
  const [supplier, label, status, score, missing, optional, assumptions, reason, groups, dropped, ambiguous,
    sourceFile, equipmentId, sourceType] = row;
  const tr = node('tr');
  tr.dataset.status = status;
  function cell(label) {
    const td = node('td');
    td.dataset.label = label;
    tr.append(td);
    return td;
  }
  cell('资料条目 / 来源 / 供应商').append(node('strong', data.strings[label]),
    node('p', '来源文件：' + (data.strings[sourceFile] || '未注明来源文件'), 'muted'),
    node('p', data.suppliers[supplier], 'muted'));
  const issues = cell('缺什么 / 待确认');
  issues.append(node('p', missing.length ? '缺：' + missing.map(i => data.strings[i]).join('、') : '必需项齐备'));
  if (reason) issues.append(node('p', data.reasons[reason], 'conflict'));
  assumptions.forEach(i => issues.append(node('p', data.strings[i], 'estimable')));
  if (optional.length) issues.append(node('p', '可选待补：' + optional.map(i => data.strings[i]).join('、'), 'muted'));
  if (dropped || ambiguous) issues.append(node('p', '数值超出范围，暂未采用 ' + dropped + ' 项；单位不明确，暂未采用 ' + ambiguous + ' 项', 'muted'));
  cell('完整度').append(node('strong', score + '/100'));
  cell('状态').append(node('span', data.statuses[status], 'status ' + status));
  const evidence = cell('数值与来源（点击展开）');
  if (!groups.length) evidence.append(node('span', '尚未识别到有标签的有效包装资料', 'muted'));
  const technical = node('details', undefined, 'technical');
  technical.append(node('summary', '技术详情'));
  const fullId = data.strings[equipmentId];
  const copyButton = node('button', '复制完整编号');
  copyButton.type = 'button';
  const feedback = node('span', '', 'copy-status');
  copyButton.addEventListener('click', () => copyId(fullId, feedback, technical));
  technical.append(node('p', 'equipment_id：' + shorten(fullId)), copyButton, feedback,
    node('p', '来源类型：' + (data.strings[sourceType] || '未注明')));
  groups.forEach(([kind, groupReason, candidates]) => {
    const details = node('details');
    details.append(node('summary', data.kinds[kind] + '：' +
      [...new Set(candidates.map(c => formatValue(kind, c[0])))].join('；')));
    if (groupReason) details.append(node('p', data.reasons[groupReason], 'conflict'));
    candidates.forEach(([value, raw, chunk, inferred, confidence]) => {
      details.append(node('p', inferred ? '单位由资料推断，须确认' : '原文已注明单位或装箱数量'));
      const source = node('div', undefined, 'source');
      source.append(node('p', data.kinds[kind] + '：' + formatValue(kind, value)),
        node('small', 'chunk_id：' + data.strings[chunk] + ' · ' +
          (inferred ? '单位推断，须确认' : '原文有单位/明确装箱标签') + ' · confidence：' + confidence),
        node('blockquote', data.strings[raw]));
      technical.append(source);
    });
    evidence.append(details);
  });
  evidence.append(technical);
  return tr;
}
function renderProducts() {
  const query = search.value.trim().toLocaleLowerCase();
  const filtered = data.rows.filter(row => matches(row, query, statusFilter.value, supplierFilter.value));
  const pages = Math.max(1, Math.ceil(filtered.length / data.page_size));
  page = Math.max(1, Math.min(page, pages));
  const start = (page - 1) * data.page_size;
  body.replaceChildren(...filtered.slice(start, start + data.page_size).map(renderRow));
  document.getElementById('visible-count').textContent = '符合条件 ' + filtered.length +
    ' 条；第 ' + page + ' / ' + pages + ' 页；本页 ' + Math.min(data.page_size, filtered.length - start) + ' 条';
  document.getElementById('no-results').hidden = filtered.length !== 0;
  previous.disabled = page === 1;
  next.disabled = page === pages;
}
function resetProducts() { page = 1; renderProducts(); }
search.addEventListener('input', resetProducts);
statusFilter.addEventListener('change', resetProducts);
supplierFilter.addEventListener('change', resetProducts);
previous.addEventListener('click', () => { page--; renderProducts(); });
next.addEventListener('click', () => { page++; renderProducts(); });
document.querySelectorAll('button[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const area = document.getElementById(button.dataset.copy);
    const feedback = button.parentElement.querySelector('.copy-status');
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error('fallback');
      await navigator.clipboard.writeText(area.value);
      feedback.textContent = '草稿已复制，尚未发送';
    } catch (_) {
      area.closest('details').open = true;
      area.focus();
      area.select();
      let copied = false;
      try { copied = document.execCommand('copy'); } catch (_) {}
      feedback.textContent = copied ? '草稿已复制，尚未发送' : '已选中草稿，请使用系统复制操作或长按复制';
    }
  });
});
renderProducts();
'''


def action_hint(report):
    if not report['summary']['total']:
        return '今天该怎么用：暂无资料条目，请先补充供应商资料。'
    pending = [entry for entry in report['supplier_coverage']
               if report['followups'].get(entry['supplier'])]
    # Rank by the size of the gap (entries without packing data), not by rate,
    # so one-entry suppliers with 0% coverage don't outrank large suppliers.
    priority = sorted(pending, key=lambda entry: (
        -entry['without_packing'], entry['coverage_rate'], entry['supplier']))[:3]
    if priority:
        hint = '优先联系' + '、'.join(entry['supplier'] for entry in priority) + '，补充或确认包装资料。'
    else:
        hint = '现有条目的包装资料已齐全，报价前仍须人工核对来源和包装方案。'
    mixed = report['summary']['by_conflict_reason']['multi_model_suspected']
    if mixed:
        hint += f'有 {mixed} 条资料疑似混入多个型号，建议拆分后再核对。'
    return '今天该怎么用：' + hint


def render_html(report):
    summary = report['summary']
    total = summary['total']
    cards = []
    for label, count in (
        ('资料条目', total), (STATUS_LABELS['ready'], summary['ddp_ready']),
        (STATUS_LABELS['estimable'], summary['ddp_estimable']),
        ('资料不全', summary['by_status']['partial'] + summary['by_status']['missing']),
        ('冲突：疑似混入多个型号', summary['by_conflict_reason']['multi_model_suspected']),
        ('冲突：同一项数值不一致', summary['by_conflict_reason']['value_mismatch']),
    ):
        percent = round(count / total * 100, 1) if total else 0
        explanation = ('<small>从供应商文档、报价表和聊天中抽出的规格条目，含文档碎片，不等于产品款数</small>'
                       if label == '资料条目' else '')
        cards.append(f'<div class="card"><div>{_e(label)}</div><strong>{count}</strong>{explanation}'
                     f'<small>占全部条目 {percent:g}%</small><div class="bar" aria-hidden="true">'
                     f'<span data-w="{percent}"></span></div></div>')
    ranking, sections, supplier_options = [], [], []
    for index, entry in enumerate(report['supplier_coverage']):
        supplier = entry['supplier']
        rate = entry['coverage_rate'] * 100
        cells = [('供应商', supplier), ('资料条目', entry['total']),
                 ('有包装资料条目', entry['with_packing']), ('无包装资料条目', entry['without_packing'])]
        ranking.append('<tr>' + ''.join(f'<td data-label="{label}">{_e(value)}</td>' for label, value in cells)
                       + f'<td data-label="覆盖率">{rate:.1f}%<div class="bar" aria-hidden="true">'
                       + f'<span data-w="{rate:.1f}"></span></div></td>'
                       + f'<td data-label="{STATUS_LABELS["estimable"]}">{entry["ddp_estimable"]}</td>'
                       + f'<td data-label="{STATUS_LABELS["ready"]}">{entry["ddp_ready"]}</td></tr>')
        supplier_options.append(f'<option value="{index}">{_e(supplier)}</option>')
        draft = report['followups'][supplier]
        draft_id = f'draft-{index}'
        copy = (f'<button type="button" data-copy="{draft_id}">复制追问草稿</button>'
                '<span class="copy-status" role="status" aria-live="polite"></span>') if draft else (
                    '<button type="button" disabled>复制追问草稿（暂无待追问项）</button>')
        draft_area = (f'<details class="draft"><summary>查看追问草稿（本供应商全部待处理条目）</summary>'
                      f'<textarea id="{draft_id}" readonly aria-label="供应商追问草稿"></textarea>'
                      '</details>') if draft else ''
        sections.append('<section class="supplier"><div class="supplier-head"><h3>' + _e(supplier)
                        + f'</h3><div>{copy}</div></div>{draft_area}</section>')
    options = ''.join(f'<option value="{key}">{_e(STATUS_LABELS[key])}</option>'
                      for key in ('ready', 'estimable'))
    options += ('<option value="incomplete">资料不全（全部）</option>'
                '<option value="partial">资料不全（已有部分资料）</option>'
                '<option value="missing">资料不全（暂无包装资料）</option>'
                '<option value="conflict">数值有冲突</option>')
    reason_options = ''.join(f'<option value="{key}">{_e(label)}</option>'
                             for key, label in CONFLICT_REASON_LABELS.items())
    # This is element text, not an attribute: quotes are safe without entities.
    # Escape <, > and & to prevent template termination and preserve entities.
    # The DOM decodes once, then JSON.parse reads it; data only enters text nodes.
    payload = escape(json.dumps(_html_data(report), ensure_ascii=False,
                                separators=(',', ':'), allow_nan=False), quote=False)
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>供应商包装资料核对</title><style>' + CSS + '</style></head><body><main>'
            '<h1>供应商包装资料核对</h1><p>' + _e(action_hint(report))
            + '</p><p class="muted">生成时间（世界标准时间）：'
            + _e(report['generated_at'].replace('T', ' ').replace('+00:00', ''))
            + '</p><div class="notice"><strong>' + _e(report['notice']) + '</strong>'
            '<p>可直接报价（资料齐全）：外箱或航空箱三边尺寸、毛重、每箱数量已注明且无冲突。'
            '可估算：尺寸和毛重齐备、每箱数量未注明，按每箱 1 台估算，须向供应商确认。'
            '任何冲突均须先确认；使用前核对单位、来源及包装方案。</p></div>'
            '<div class="cards">' + ''.join(cards) + '</div>'
            '<p class="muted">资料不全包含已有部分资料和暂无包装资料的条目；两类冲突按条目分别计数，每条仅归入一类，含多个型号疑点的归入疑似多型号。'
            '逐项原因见明细。分数沿用尺寸 30、毛重 30、装箱数 20、可选净重和产品尺寸各 10，冲突项不计分；分数不代替资料门槛。</p>'
            '<h2>供应商资料覆盖排行</h2><p class="muted">覆盖率低的在前；有包装资料指至少识别到一项有效包装信息，包含有冲突的资料。以下均为条目数，不等于产品款数。</p>'
            '<div class="table-wrap"><table><thead><tr><th>供应商</th><th>资料条目</th><th>有包装资料条目</th>'
            '<th>无包装资料条目</th><th>覆盖率</th>'
            f'<th>{STATUS_LABELS["estimable"]}</th><th>{STATUS_LABELS["ready"]}</th></tr></thead><tbody>' + ''.join(ranking)
            + '</tbody></table></div><h2>资料条目明细</h2>'
            '<p class="muted">默认显示已有部分资料、可估算和有冲突的条目，每页 50 条。暂无包装资料的条目默认只在供应商维度计数；'
            '切换状态可查看这些条目或全部条目。</p><div class="controls">'
            '<label>搜索<input id="search" type="search" placeholder="名称、来源文件名或供应商"></label>'
            '<label>状态<select id="status-filter"><option value="action" selected>需要处理（不含无资料）</option>'
            '<option value="all">全部状态</option>' + options + reason_options + '</select></label>'
            '<label>供应商<select id="supplier-filter"><option value="">全部供应商</option>'
            + ''.join(supplier_options) + '</select></label></div>'
            '<div class="pagination"><button id="previous" type="button">上一页</button>'
            '<span id="visible-count" role="status" aria-live="polite"></span>'
            '<button id="next" type="button">下一页</button></div>'
            '<div class="table-wrap"><table class="products"><thead><tr><th>资料条目 / 来源 / 供应商</th><th>缺什么 / 待确认</th>'
            '<th>完整度</th><th>状态</th><th>数值与来源</th></tr></thead><tbody id="product-rows"></tbody></table></div>'
            '<p id="no-results" class="empty" hidden>没有符合条件的资料条目。</p>'
            '<noscript>请启用浏览器脚本查看分页明细和复制草稿；完整候选、来源及草稿见同目录完整数据报告。供应商排行可直接查看。</noscript>'
            '<h2>按供应商追问</h2><p class="muted">筛选和翻页不会改变草稿；暂无包装资料的条目合并概括。</p>'
            + ''.join(sections) + '<p class="foot muted">追问仅为文字草稿，未发送。本报告不计算运费，不代表已确认报价。'
            '数值超出范围，暂未采用 ' + str(summary['dropped_out_of_range']) + ' 项；单位不明确，暂未采用 '
            + str(summary['dropped_ambiguous']) + ' 项。</p></main>'
            '<template id="report-data">' + payload + '</template><script>' + JS + '</script></body></html>')
