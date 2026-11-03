// Offline DOM double for exercising the report's actual embedded UI code.
// No browser package, network, external resources or user data is required.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.listeners = {};
    this.value = '';
    this.text = '';
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  append(...children) { children.forEach(child => { child.parentElement = this; }); this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() {}
  select() { this.selected = true; }
  remove() { this.parentElement.children = this.parentElement.children.filter(child => child !== this); }
}
const elements = {};
for (const id of ['report-data', 'search', 'status-filter', 'supplier-filter', 'product-rows',
                  'previous', 'next', 'visible-count', 'no-results']) elements[id] = new Element();
elements['report-data'].content = {textContent: JSON.stringify(input.data)};
elements['status-filter'].value = input.data.default_filter;
input.data.drafts.forEach((lines, index) => { elements['draft-' + index] = new Element('textarea'); });
const createdTags = [];
const copied = [];
const document = {
  getElementById(id) {
    if (!elements[id]) throw new Error('Unknown element ' + id);
    return elements[id];
  },
  createElement(tag) { createdTags.push(tag); return new Element(tag); },
  querySelectorAll() { return []; },
  execCommand(command) {
    if (input.copyMode === 'manual') return false;
    const area = descendants(elements['product-rows']).find(element => element.selected);
    if (command === 'copy' && area) { copied.push(area.value); return true; }
    return false;
  }
};
const context = vm.createContext({document,
  navigator: input.copyMode === 'clipboard' ? {clipboard: {writeText: async value => { copied.push(value); }}} : {},
  window: {isSecureContext: input.copyMode === 'clipboard'}
});
vm.runInContext(input.script, context, {timeout: 5000});
function descendants(element) { return [element, ...element.children.flatMap(descendants)]; }
function businessText(element) {
  if (element.className === 'technical') return '';
  return element.text + element.children.map(businessText).join('');
}
function snapshot() {
  return {
    rows: elements['product-rows'].children.map(row => ({
      status: row.dataset.status, label: row.children[0].children[0].textContent,
      text: row.textContent, businessText: businessText(row),
      technical: descendants(row).filter(element => element.className === 'technical').map(element => ({
        text: element.textContent, open: !!element.open,
        copyButtons: descendants(element).filter(child => child.tagName === 'button').map(child => child.textContent),
        selectedValues: descendants(element).filter(child => child.selected).map(child => child.value)
      }))
    })),
    count: elements['visible-count'].textContent,
    previousDisabled: elements.previous.disabled, nextDisabled: elements.next.disabled,
    empty: !elements['no-results'].hidden
  };
}
const snapshots = [snapshot()];
(async () => {
  for (const event of input.events || []) {
    const element = event.id === 'copy-first-id'
      ? descendants(elements['product-rows']).find(element => element.tagName === 'button') : elements[event.id];
    if ('value' in event) element.value = event.value;
    await element.listeners[event.type]();
    snapshots.push(snapshot());
  }
  const drafts = input.data.drafts.map((_, index) => elements['draft-' + index].value);
  process.stdout.write(JSON.stringify({snapshots, createdTags, drafts, copied}));
})();
