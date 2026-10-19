const charts = new WeakMap();

const moneyFormat = new Intl.NumberFormat('zh-CN', {
  style: 'currency',
  currency: 'USD'
});
const percentFormat = new Intl.NumberFormat('zh-CN', {
  style: 'percent',
  maximumFractionDigits: 2
});

function number(value) {
  if (value == null) return 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function segments(totals, masked) {
  const price = number(totals.price_usd);
  const freight = number(totals.freight_usd);
  const duty = number(totals.duty_vat_usd);
  if (masked) {
    return {
      price,
      items: [
        { name: '运费', value: freight, color: 'freight' },
        { name: '关税增值税', value: duty, color: 'duty' },
        { name: '其余', value: number(price - freight - duty), color: 'rest' }
      ]
    };
  }
  return {
    price,
    items: [
      { name: '货值', value: number(totals.goods_cost_usd), color: 'goods' },
      { name: '包装', value: number(totals.packaging_cost_usd), color: 'packaging' },
      { name: '配件', value: number(totals.accessory_cost_usd), color: 'accessory' },
      { name: '运费', value: freight, color: 'freight' },
      { name: '关税增值税', value: duty, color: 'duty' },
      { name: '毛利', value: number(price - number(totals.cost_usd)), color: 'profit' }
    ]
  };
}

function palette(state) {
  const theme = document.documentElement.dataset.bsTheme;
  const dark = theme === 'dark' || (theme !== 'light' && state.media.matches);
  const style = window.getComputedStyle(state.el);
  const css = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  return {
    text: css('--tblr-body-color', dark ? '#e6edf5' : '#182433'),
    background: css('--tblr-bg-surface', dark ? '#182433' : '#ffffff'),
    border: css('--tblr-border-color', dark ? '#354355' : '#dce1e7'),
    goods: css('--tblr-primary', dark ? '#74b0ff' : '#206bc4'),
    packaging: css('--tblr-info', dark ? '#72c6ef' : '#4299e1'),
    accessory: css('--tblr-purple', dark ? '#b49bea' : '#ae3ec9'),
    freight: css('--tblr-warning', dark ? '#f7bd50' : '#f59f00'),
    duty: css('--tblr-orange', dark ? '#ff9c65' : '#f76707'),
    profit: css('--tblr-success', dark ? '#72cf81' : '#2fb344'),
    rest: css('--tblr-secondary', dark ? '#a3afbd' : '#667382')
  };
}

function percentage(value, price) {
  return price === 0 ? '占比不可计算' : percentFormat.format(number(value / price));
}

function description(item, state) {
  const ratio = percentage(item.value, state.data.price);
  return state.masked
    ? `${item.name}：${ratio}`
    : `${item.name}：${moneyFormat.format(item.value)}（${ratio}）`;
}

function paint(state) {
  if (state.disposed) return;
  const colors = palette(state);
  const items = state.data.items;
  state.chart.dispatchAction({ type: 'hideTip' });
  state.chart.setOption({
    animation: false,
    backgroundColor: 'transparent',
    textStyle: { color: colors.text },
    grid: { left: 4, right: 4, top: 8, bottom: 8, containLabel: false },
    xAxis: {
      type: 'value',
      show: false,
      splitLine: { show: false }
    },
    yAxis: { type: 'category', data: ['报价构成'], show: false },
    tooltip: {
      trigger: 'item',
      renderMode: 'richText',
      confine: true,
      backgroundColor: colors.background,
      borderColor: colors.border,
      textStyle: { color: colors.text },
      formatter(params) {
        const item = state.data.items[params.seriesIndex];
        return item ? description(item, state) : '';
      }
    },
    series: items.map((item) => ({
      name: item.name,
      type: 'bar',
      stack: 'cost',
      barWidth: '100%',
      data: [item.value],
      itemStyle: { color: colors[item.color] },
      emphasis: { focus: 'self' },
      label: { show: false }
    }))
  }, { notMerge: true, lazyUpdate: false });
  state.el.setAttribute('aria-label', `成本构成；${items.map((item) => description(item, state)).join('；')}`);
}

export function renderCostChart(el, totals = {}, { masked = false } = {}) {
  const echarts = window.echarts;
  if (!echarts || typeof echarts.init !== 'function') {
    disposeCostChart(el);
    el.hidden = true;
    return;
  }

  let state = charts.get(el);
  if (state && typeof state.chart.isDisposed === 'function' && state.chart.isDisposed()) {
    disposeCostChart(el);
    state = undefined;
  }
  el.hidden = false;

  if (!state) {
    el.textContent = '';
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    state = {
      el,
      chart: echarts.init(el, null, {renderer:'canvas'}),
      media,
      masked: masked === true,
      data: segments(totals || {}, masked === true),
      disposed: false,
      themeObserver: null,
      resizeObserver: null,
      onTheme: null,
      onResize: null,
      modernMedia: typeof media.addEventListener === 'function'
    };
    charts.set(el, state);
    state.onTheme = () => paint(state);
    state.onResize = () => {
      if (!state.disposed) state.chart.resize();
    };
    state.themeObserver = new MutationObserver(state.onTheme);
    state.themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-bs-theme']
    });
    if (state.modernMedia) {
      media.addEventListener('change', state.onTheme);
    } else {
      media.addListener(state.onTheme);
    }
    if (typeof window.ResizeObserver === 'function') {
      state.resizeObserver = new window.ResizeObserver(state.onResize);
      state.resizeObserver.observe(el);
    } else {
      window.addEventListener('resize', state.onResize);
    }
  } else {
    state.masked = masked === true;
    state.data = segments(totals || {}, state.masked);
  }

  paint(state);
  state.chart.resize();
}

export function disposeCostChart(el) {
  const state = charts.get(el);
  if (!state) return;
  charts.delete(el);
  state.disposed = true;
  state.themeObserver?.disconnect();
  state.resizeObserver?.disconnect();
  if (!state.resizeObserver) {
    window.removeEventListener('resize', state.onResize);
  }
  if (state.modernMedia) {
    state.media.removeEventListener('change', state.onTheme);
  } else {
    state.media.removeListener(state.onTheme);
  }
  if (typeof state.chart.isDisposed !== 'function' || !state.chart.isDisposed()) {
    state.chart.dispose();
  }
  state.data = null;
  el.textContent = '';
  el.setAttribute('aria-label', '成本构成图待计算');
}
