const instances = new WeakMap();
const ACTIONS = new Set(['save', 'pdf', 'whatsapp', 'zip', 'letter']);
const EXPORTS = [
  ['pdf', '下载 PDF', '.pdf'],
  ['html', '下载 HTML', '.html'],
  ['whatsapp', '下载 WhatsApp 文案', '.txt'],
  ['images_zip', '下载图片包', '-images.zip'],
];
const MESSAGES = Object.freeze({
  ACK_REQUIRED: '请先勾选确认项',
  OVERRIDE_REQUIRED: '毛利低于最低线,需主管放行并填写原因',
  MARGIN_FUSE: '毛利触及熔断线，无法保存或导出',
  PRICE_FLOOR_FUSE: '售价触及价格底线，无法保存或导出',
  NO_COST_BASIS: '缺少可用成本依据，请先补齐',
  LANE_UNPRICED: '所选航线尚未定价，请确认运价',
  LANE_EXPIRED: '所选航线运价已过期，请更新后重试',
  FX_EXPIRED: '汇率已过期，请更新后重试',
  PACKING_UNKNOWN: '包装规格尚未确认，请补齐包装资料',
  PACKING_COST_MISSING: '缺少包装成本，请补齐后重试',
  IMAGE_MISSING: '缺少可对客图片，请补齐后再导出',
  LEAK_BLOCKED: '对客文件未通过泄露检查,已拦截并记录',
  LETTER_NUMBER_MISMATCH: '函件数字与报价不一致,请重试',
  LETTER_UNAVAILABLE: '函件服务未配置',
  QUOTE_NOT_FINAL: '报价尚未冻结，无法导出或生成函件',
  QUOTE_NOT_FOUND: '报价不存在，请重新生成',
  PAYMENT_TERMS_UNKNOWN: '付款条款不可用，请确认配置',
  PAYMENT_TERMS_REQUIRED: '请选择付款条款',
  PAYMENT_TERMS_UNAVAILABLE: '付款条款暂不可用',
  BAD_REQUEST: '请求内容无效，请检查输入',
  INVALID_COUNTRY: '国家代码须为两位英文字母，未知可留空',
  INVALID_LEAD_TIME: '交期须为正整数或递增的整数区间',
  INVALID_LETTER_OPTIONS: '请检查函件语气、用途及备注长度',
  RESPONSE_INVALID: '服务端响应格式无效，请重试',
  EXPORT_URL_INVALID: '下载地址无效，请重新生成报价',
  NETWORK_ERROR: '网络请求失败，请稍后重试',
  EXPORT_UNAVAILABLE: '导出服务暂不可用，请稍后重试',
  INPUT_CHANGED: '报价内容已变更，请重新操作',
});
const LEAK_CATEGORIES = Object.freeze({
  supplier_name: '供应商名称',
  factory_model: '工厂型号',
  cny_marker: '人民币标记',
  internal_key: '内部字段',
  cost_value: '成本数值',
  pdf_meta: 'PDF 元数据',
  inspection_error: '检查异常',
  projection_error: '对客数据转换异常',
});
const owns = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const nonempty = value => typeof value === 'string' && Boolean(value.trim());

class ActionFailure extends Error {
  constructor(code, status = 0, categories = []) {
    super(code);
    this.code = code;
    this.status = status;
    this.categories = categories;
  }
}

function errorMessage(error) {
  if (error?.status === 401) return '登录状态已失效，请重新登录';
  if (error?.status === 403) return '没有操作权限或安全校验未通过，请刷新后重试';
  if (error?.status === 413) return '请求内容过大，请减少内容后重试';
  const code = error instanceof ActionFailure ? error.code : 'NETWORK_ERROR';
  const message = owns(MESSAGES, code) ? MESSAGES[code] : `操作失败:${code}`;
  if (code !== 'LEAK_BLOCKED') return message;
  const categories = [...new Set(error.categories)]
    .filter(category => owns(LEAK_CATEGORIES, category))
    .map(category => LEAK_CATEGORIES[category]);
  return categories.length ? `${message}（类别：${categories.join('、')}）` : message;
}

function lockActionButtons(actions, doc) {
  const desired = new Map([...actions.querySelectorAll('button[data-action]')]
    .map(button => [button, button.disabled]));
  for (const button of desired.keys()) button.disabled = true;
  const Observer = doc.defaultView?.MutationObserver;
  let observer = null;
  let released = false;
  function remember(records) {
    for (const record of records) {
      if (desired.has(record.target)) desired.set(record.target, record.target.disabled);
    }
  }
  if (Observer) {
    observer = new Observer(records => {
      remember(records);
      for (const button of desired.keys()) {
        if (!button.disabled) button.disabled = true;
      }
      observer.takeRecords();
    });
    observer.observe(actions, {subtree: true, attributes: true, attributeFilter: ['disabled']});
  }
  return () => {
    if (released) return;
    released = true;
    if (observer) {
      remember(observer.takeRecords());
      observer.disconnect();
    }
    for (const [button, disabled] of desired) button.disabled = disabled;
  };
}

export function initActions({root} = {}) {
  const doc = root?.nodeType === 9 ? root : root?.ownerDocument || document;
  const drawer = root?.matches?.('#cpq-drawer')
    ? root : (root || doc).querySelector?.('#cpq-drawer');
  const actions = drawer?.querySelector('#cpq-actions');
  if (!actions?.parentNode) return {destroy() {}};
  if (instances.has(actions)) return instances.get(actions);

  const cleanup = [];
  const abort = new AbortController();
  const objectURLs = new Map();
  let dead = false;
  let busy = false;
  let revision = 0;
  let lastKey = null;
  let cachedFinal = null;
  let displayedQuote = null;
  let releaseLock = null;

  function make(tag, attributes = {}, text) {
    const element = doc.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function listen(element, type, callback) {
    element.addEventListener(type, callback);
    cleanup.push(() => element.removeEventListener(type, callback));
  }
  function field(parent, caption, control, wide = false) {
    const wrapper = make('div', {class: `cpq-quote-field${wide ? ' cpq-quote-wide' : ''}`});
    wrapper.append(make('label', {for: control.id}, caption), control);
    parent.append(wrapper);
    return control;
  }
  function select(id, options) {
    const element = make('select', {id, class: 'form-select'});
    for (const [value, caption] of options) element.append(make('option', {value}, caption));
    return element;
  }

  if (!doc.getElementById('cpq-actions-styles')) {
    const stylesheet = make('link', {
      id: 'cpq-actions-styles',
      rel: 'stylesheet',
      href: new URL('../../strobe/strobe_actions.css', import.meta.url).pathname,
    });
    (doc.head || doc.documentElement).append(stylesheet);
  }

  const panel = make('section', {id: 'cpq-quote-panel', 'aria-labelledby': 'cpq-quote-title'});
  panel.append(make('h3', {id: 'cpq-quote-title'}, '报价资料与函件'));
  const controls = make('fieldset', {class: 'cpq-quote-controls'});
  const form = make('form', {id: 'cpq-buyer-form', class: 'cpq-quote-block', novalidate: ''});
  form.append(make('h4', {}, '买家与报价条款'));
  const grid = make('div', {class: 'cpq-quote-grid'});
  const company = field(grid, '买家公司', make('input', {
    id: 'cpq-buyer-company', type: 'text', class: 'form-control', autocomplete: 'organization',
  }));
  const contact = field(grid, '联系人', make('input', {
    id: 'cpq-buyer-contact', type: 'text', class: 'form-control', autocomplete: 'name',
  }));
  const country = field(grid, '国家代码（两位字母，选填）', make('input', {
    id: 'cpq-buyer-country', type: 'text', class: 'form-control',
    minlength: '2', maxlength: '2', pattern: '[A-Za-z]{2}', autocomplete: 'country',
    autocapitalize: 'characters', spellcheck: 'false',
  }));
  const payment = field(grid, '付款条款', select('cpq-payment-terms', [['TT_30_70', 'TT_30_70']]));
  payment.disabled = true;
  const leadTime = field(grid, '交期（天，应用到每个型号）', make('input', {
    id: 'cpq-lead-time', type: 'text', class: 'form-control', required: '',
    pattern: '[0-9]+(-[0-9]+)?',
  }), true);
  leadTime.value = '15-20';
  form.append(grid);
  const status = make('p', {
    id: 'cpq-quote-status', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true',
  });
  const result = make('div', {id: 'cpq-quote-result'});
  const letter = make('section', {id: 'cpq-letter', class: 'cpq-quote-block', 'aria-labelledby': 'cpq-letter-title'});
  letter.append(make('h4', {id: 'cpq-letter-title'}, '英文函件'));
  const letterGrid = make('div', {class: 'cpq-quote-grid'});
  const tone = field(letterGrid, '语气', select('cpq-letter-tone', [
    ['formal', '正式'], ['friendly', '友好'],
  ]));
  const purpose = field(letterGrid, '用途', select('cpq-letter-purpose', [
    ['first_quote', '首次报价'], ['follow_up', '跟进'], ['revised_quote', '修订报价'],
  ]));
  const notes = field(letterGrid, '备注（最多 500 字）', make('textarea', {
    id: 'cpq-letter-notes', class: 'form-control', maxlength: '500', rows: '3',
  }), true);
  letter.append(letterGrid);
  const editor = make('div', {class: 'cpq-letter-editor'});
  editor.hidden = true;
  const subject = field(editor, '主题', make('input', {
    id: 'cpq-letter-subject', type: 'text', class: 'form-control',
  }));
  const body = field(editor, '正文（可编辑）', make('textarea', {
    id: 'cpq-letter-body', class: 'form-control', rows: '10',
  }));
  editor.append(make('button', {
    type: 'button', class: 'cpq-copy-button', 'data-cpq-copy': 'letter',
    'aria-label': '复制函件主题和正文',
  }, '复制'));
  letter.append(editor);
  controls.append(form, status, result, letter);
  panel.append(controls);
  actions.parentNode.insertBefore(panel, actions.nextSibling);
  listen(form, 'submit', event => event.preventDefault());

  function setStatus(text, state = '') {
    if (dead) return;
    status.textContent = text;
    status.setAttribute('data-state', state);
  }
  function resetLetter() {
    editor.hidden = true;
    subject.value = '';
    body.value = '';
  }
  function clearOutputs() {
    displayedQuote = null;
    result.textContent = '';
    resetLetter();
  }
  function invalidate() {
    const hadOutput = Boolean(displayedQuote || result.textContent || cachedFinal);
    revision += 1;
    lastKey = null;
    cachedFinal = null;
    clearOutputs();
    if (hadOutput || busy) setStatus(MESSAGES.INPUT_CHANGED);
  }
  function syncLinks() {
    for (const link of result.querySelectorAll('a[data-cpq-export]')) {
      if (busy) {
        link.setAttribute('aria-disabled', 'true');
        link.setAttribute('tabindex', '-1');
      } else {
        link.removeAttribute('aria-disabled');
        link.removeAttribute('tabindex');
      }
    }
  }
  async function run(task) {
    if (dead || busy) return;
    busy = true;
    controls.disabled = true;
    panel.setAttribute('aria-busy', 'true');
    syncLinks();
    const unlock = lockActionButtons(actions, doc);
    releaseLock = unlock;
    const current = revision;
    const guard = () => {
      if (dead || current !== revision) throw new ActionFailure('INPUT_CHANGED');
    };
    setStatus('处理中…', 'pending');
    try {
      await task(guard);
    } catch (error) {
      if (!dead && current === revision) setStatus(errorMessage(error), 'error');
    } finally {
      unlock();
      releaseLock = null;
      busy = false;
      controls.disabled = false;
      if (!dead) {
        panel.setAttribute('aria-busy', 'false');
        syncLinks();
      }
    }
  }

  async function request(url, payload) {
    const options = {
      credentials: 'same-origin', redirect: 'error', cache: 'no-store', signal: abort.signal,
    };
    if (payload !== undefined) {
      options.method = 'POST';
      options.headers = {'Content-Type': 'application/json'};
      const csrf = doc.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
      if (csrf) options.headers['X-CSRF-Token'] = csrf;
      options.body = JSON.stringify(payload);
    }
    const reply = await fetch(url, options);
    if (!reply.ok) {
      let data = null;
      try { data = await reply.json(); } catch { /* Error bodies may be empty. */ }
      const raw = data?.error;
      const code = typeof raw?.code === 'string' && /^[A-Z][A-Z0-9_]*$/.test(raw.code)
        ? raw.code : `HTTP_${reply.status}`;
      const categories = Array.isArray(raw?.details?.categories)
        ? raw.details.categories.filter(value => typeof value === 'string' && owns(LEAK_CATEGORIES, value))
        : [];
      throw new ActionFailure(code, reply.status, categories);
    }
    return reply;
  }
  async function json(url, payload) {
    const reply = await request(url, payload);
    let data;
    try { data = await reply.json(); } catch { throw new ActionFailure('RESPONSE_INVALID'); }
    if (!isObject(data)) throw new ActionFailure('RESPONSE_INVALID');
    return data;
  }

  setStatus('处理中…', 'pending');
  const contextTask = (async () => {
    try {
      const data = await json('/api/cpq/context');
      if (dead) return;
      const terms = Array.isArray(data.payment_terms) ? data.payment_terms : [];
      const seen = new Set();
      const usable = terms.filter(item => {
        if (!nonempty(item?.id) || item.id !== item.id.trim() || !nonempty(item.en) || seen.has(item.id)) return false;
        seen.add(item.id);
        return true;
      });
      if (!usable.length) throw new ActionFailure('PAYMENT_TERMS_UNAVAILABLE');
      payment.textContent = '';
      for (const item of usable) payment.append(make('option', {value: item.id}, item.en));
      if (seen.has('TT_30_70')) payment.value = 'TT_30_70';
      if (!busy) setStatus('付款条款已加载');
    } catch (error) {
      if (!dead) {
        payment.textContent = '';
        payment.append(make('option', {value: 'TT_30_70'}, 'TT_30_70'));
        if (!busy) setStatus(`${errorMessage(error)}；付款条款暂用 TT_30_70`, 'error');
      }
    } finally {
      if (!dead) payment.disabled = false;
    }
  })();

  function snapshotDetail(detail) {
    try {
      const value = JSON.parse(JSON.stringify({
        request: detail.request, acks: detail.acks, override_reason: detail.override_reason,
      }));
      if (!isObject(value.request) || !Array.isArray(value.request.lines) || !value.request.lines.length) {
        throw new ActionFailure('BAD_REQUEST');
      }
      return value;
    } catch {
      throw new ActionFailure('BAD_REQUEST');
    }
  }
  function buildPayload(detail, draft) {
    const countryCode = country.value.trim().toUpperCase();
    if (countryCode && !/^[A-Z]{2}$/.test(countryCode)) {
      throw new ActionFailure('INVALID_COUNTRY');
    }
    const lead = leadTime.value.trim();
    if (!/^[0-9]+(?:-[0-9]+)?$/.test(lead)) throw new ActionFailure('INVALID_LEAD_TIME');
    const bounds = lead.split('-').map(value => BigInt(value));
    if (bounds.some(value => value <= 0n) || bounds[bounds.length - 1] < bounds[0]) {
      throw new ActionFailure('INVALID_LEAD_TIME');
    }
    const lines = detail.request.lines;
    if (lines.some(line => !nonempty(line?.model_id))) throw new ActionFailure('BAD_REQUEST');
    const acks = detail.acks ?? detail.request.acks ?? [];
    const reason = detail.override_reason ?? detail.request.override_reason ?? '';
    if (!Array.isArray(acks) || acks.some(value => typeof value !== 'string') || typeof reason !== 'string') {
      throw new ActionFailure('BAD_REQUEST');
    }
    if (!payment.value) throw new ActionFailure('PAYMENT_TERMS_REQUIRED');
    country.value = countryCode;
    return {
      ...detail.request,
      buyer: {company: company.value.trim(), contact: contact.value.trim(), country: countryCode},
      payment_terms_id: payment.value,
      lead_time_days: Object.fromEntries(lines.map(line => [line.model_id, lead])),
      acks,
      override_reason: reason,
      draft,
    };
  }
  function letterOptions() {
    if (!['formal', 'friendly'].includes(tone.value)
      || !['first_quote', 'follow_up', 'revised_quote'].includes(purpose.value)
      || notes.value.length > 500) throw new ActionFailure('INVALID_LETTER_OPTIONS');
    return {tone: tone.value, purpose: purpose.value, extra_notes: notes.value};
  }
  function exportURL(value) {
    if (typeof value !== 'string' || !/^\/(?!\/)/.test(value)
      || /[\\\s\u0000-\u001f\u007f]/u.test(value)) throw new ActionFailure('EXPORT_URL_INVALID');
    let parsed;
    try { parsed = new URL(value, doc.location.href); } catch { throw new ActionFailure('EXPORT_URL_INVALID'); }
    if (parsed.origin !== doc.location.origin) throw new ActionFailure('EXPORT_URL_INVALID');
    return value;
  }
  function checkedQuote(data, draft) {
    if (!/^q_[0-9a-f]{32}$/.test(data.quote_token || '')
      || !nonempty(data.quote_no) || !nonempty(data.valid_until)) throw new ActionFailure('RESPONSE_INVALID');
    if (data.status !== (draft ? 'draft' : 'final')) throw new ActionFailure('QUOTE_NOT_FINAL');
    const exports = {};
    if (!draft) {
      if (data.gate?.can_export !== true) throw new ActionFailure('QUOTE_NOT_FINAL');
      if (!isObject(data.exports)) throw new ActionFailure('RESPONSE_INVALID');
      for (const [key] of EXPORTS) exports[key] = exportURL(data.exports[key]);
    }
    return {
      quote_token: data.quote_token, quote_no: data.quote_no,
      valid_until: data.valid_until, status: data.status, exports,
    };
  }
  function filename(quote, key) {
    const suffix = EXPORTS.find(item => item[0] === key)?.[2];
    if (!suffix) throw new ActionFailure('EXPORT_URL_INVALID');
    return quote.quote_no.replace(/[^A-Za-z0-9_-]/g, '_') + suffix;
  }
  function renderQuote(quote) {
    if (displayedQuote?.quote_token !== quote.quote_token) resetLetter();
    result.textContent = '';
    displayedQuote = quote.status === 'final' ? quote : null;
    if (quote.status === 'draft') {
      result.append(make('p', {}, '草稿已保存,不可导出'));
      return;
    }
    const metadata = make('dl', {class: 'cpq-quote-meta'});
    metadata.append(
      make('dt', {}, '报价号'), make('dd', {}, quote.quote_no),
      make('dt', {}, '有效期至'), make('dd', {}, quote.valid_until),
    );
    const links = make('div', {class: 'cpq-quote-links'});
    for (const [key, caption] of EXPORTS) {
      links.append(make('a', {
        href: quote.exports[key], download: filename(quote, key),
        class: 'cpq-export-link', 'data-cpq-export': key,
      }, caption));
    }
    result.append(metadata, links);
    syncLinks();
  }
  async function downloadExport(quote, key, guard) {
    const reply = await request(exportURL(quote.exports[key]));
    const blob = await reply.blob();
    guard();
    const url = URL.createObjectURL(blob);
    const link = make('a', {href: url, download: filename(quote, key)});
    link.hidden = true;
    (doc.body || doc.documentElement).append(link);
    try {
      link.click();
    } finally {
      link.remove();
      const timer = setTimeout(() => {
        URL.revokeObjectURL(url);
        objectURLs.delete(url);
      }, 1000);
      objectURLs.set(url, timer);
    }
  }
  async function showWhatsApp(quote, guard) {
    const reply = await request(exportURL(quote.exports.whatsapp));
    const text = await reply.text();
    guard();
    const block = make('div', {class: 'cpq-whatsapp-preview'});
    const area = field(block, 'WhatsApp 文案（只读）', make('textarea', {
      id: 'cpq-whatsapp-text', class: 'form-control', rows: '8', readonly: '',
    }));
    area.value = text;
    block.append(make('button', {
      type: 'button', class: 'cpq-copy-button', 'data-cpq-copy': 'whatsapp',
      'aria-label': '复制 WhatsApp 文案',
    }, '复制'));
    result.append(block);
  }
  async function copyText(text, target, fallbackMessage) {
    if (!text) return setStatus('没有可复制的内容', 'error');
    const current = revision;
    try {
      const clipboard = doc.defaultView?.navigator?.clipboard;
      if (!clipboard?.writeText) throw new Error('Clipboard unavailable');
      await clipboard.writeText(text);
      if (!dead && !busy && revision === current) setStatus('已复制', 'ok');
    } catch {
      if (!dead && !busy && revision === current) {
        target.focus();
        target.select();
        setStatus(fallbackMessage, 'error');
      }
    }
  }

  listen(doc, 'xl:cpq-action', event => {
    const action = event.detail?.action;
    if (!ACTIONS.has(action) || dead || busy) return;
    void run(async guard => {
      const detail = snapshotDetail(event.detail);
      await contextTask;
      guard();
      const payload = buildPayload(detail, action === 'save');
      const options = action === 'letter' ? letterOptions() : null;
      const finalPayload = {...payload, draft: false};
      const key = JSON.stringify(finalPayload);
      if (lastKey !== key) {
        cachedFinal = null;
        clearOutputs();
        lastKey = key;
      }
      if (action === 'save') {
        const data = await json('/api/cpq/quotes', payload);
        guard();
        renderQuote(checkedQuote(data, true));
        setStatus('草稿已保存', 'ok');
        return;
      }
      let quote = cachedFinal?.key === key ? cachedFinal.quote : null;
      if (!quote) {
        const data = await json('/api/cpq/quotes', finalPayload);
        guard();
        quote = checkedQuote(data, false);
        cachedFinal = {key, quote};
      }
      guard();
      renderQuote(quote);
      if (action === 'pdf' || action === 'zip') {
        await downloadExport(quote, action === 'pdf' ? 'pdf' : 'images_zip', guard);
        guard();
        setStatus('下载已开始', 'ok');
      } else if (action === 'whatsapp') {
        await showWhatsApp(quote, guard);
        guard();
        setStatus('文案已生成，可复制', 'ok');
      } else if (action === 'letter') {
        const data = await json(`/api/quotes/${encodeURIComponent(quote.quote_token)}/letter`, options);
        guard();
        if (typeof data.subject !== 'string' || typeof data.body !== 'string') {
          throw new ActionFailure('RESPONSE_INVALID');
        }
        subject.value = data.subject;
        body.value = data.body;
        editor.hidden = false;
        setStatus('函件已生成，可编辑后复制', 'ok');
      }
    });
  });

  listen(panel, 'click', event => {
    const link = event.target?.closest?.('a[data-cpq-export]');
    if (link && result.contains(link)) {
      event.preventDefault();
      if (busy || !displayedQuote) return;
      const quote = displayedQuote;
      const key = link.getAttribute('data-cpq-export');
      if (!EXPORTS.some(item => item[0] === key)) return;
      void run(async guard => {
        await downloadExport(quote, key, guard);
        guard();
        setStatus('下载已开始', 'ok');
      });
      return;
    }
    const button = event.target?.closest?.('button[data-cpq-copy]');
    if (!button || !panel.contains(button) || busy || dead || button.disabled) return;
    if (button.getAttribute('data-cpq-copy') === 'letter') {
      const text = subject.value ? `${subject.value}\n\n${body.value}` : body.value;
      void copyText(text, body, '自动复制不可用，请手动复制主题和正文');
    } else {
      const area = result.querySelector('#cpq-whatsapp-text');
      if (area) void copyText(area.value, area, '自动复制不可用，请手动复制所选文案');
    }
  });
  function onInput(event) {
    const target = event.target;
    if (!target?.matches) return;
    if (panel.contains(target)) {
      if (form.contains(target)) invalidate();
    } else if (target.matches('input, select, textarea') && !target.disabled) {
      invalidate();
    }
  }
  listen(drawer, 'input', onInput);
  listen(drawer, 'change', onInput);
  listen(drawer, 'click', event => {
    const button = event.target?.closest?.('button');
    if (button && !button.disabled && button.matches(
      '[data-line-action="remove"], .cpq-pack [data-mode], #cpq-mode [data-mode], #cpq-incoterm [data-incoterm]',
    )) invalidate();
  });
  listen(doc, 'xl:add-to-basket', invalidate);

  const api = {
    destroy() {
      if (dead) return;
      dead = true;
      revision += 1;
      abort.abort();
      releaseLock?.();
      cleanup.forEach(dispose => dispose());
      for (const [url, timer] of objectURLs) {
        clearTimeout(timer);
        URL.revokeObjectURL(url);
      }
      objectURLs.clear();
      cachedFinal = null;
      displayedQuote = null;
      panel.remove();
      instances.delete(actions);
    },
  };
  instances.set(actions, api);
  return api;
}
