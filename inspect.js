(element) => {
  const describe = (el, kind, label = '', extra = {}) => ({host: el.ownerDocument.location.hostname, kind,
    label: (label || '').replace(/\s+/g, ' ').trim().slice(0, 200), submits: false, files: [], answers: 0, ...extra});
  let el = element;
  if (!el) {
    el = document.activeElement || document.body;
    for (;;) {
      if (el.shadowRoot?.activeElement) { el = el.shadowRoot.activeElement; continue; }
      if (!/^I?FRAME$/.test(el.tagName)) break;
      if (!el.contentDocument) return describe(el, 'frame');
      el = el.contentDocument.activeElement || el.contentDocument.body;
    }
    if (el === el.ownerDocument.body) return describe(el, 'none');
  }
  if ((el.closest('label')?.control || el).matches('input[type=checkbox], input[type=radio], [role=checkbox], [role=radio], [role=switch]'))
    return describe(el, 'toggle');
  const entry = el.closest('textarea, select, input:not([type=submit]):not([type=button]):not([type=image]):not([type=reset]):not([type=file]), '
    + '[contenteditable]:not([contenteditable=false]), [role=textbox], [role=searchbox], [role=combobox]');
  if (entry) {
    const names = [entry.getAttribute('aria-label'), entry.labels?.[0]?.innerText, entry.placeholder, entry.name, entry.id];
    const secret = entry.type === 'password' || /(^|\s)(cc-|one-time-code)/.test(entry.getAttribute('autocomplete') || '');
    const search = entry.type === 'search' || entry.getAttribute('role') === 'searchbox' || !!entry.closest('[role=search], search')
      || /search/i.test(names.join(' '));
    return describe(el, secret ? 'secret' : search ? 'search' : 'field', names.find(Boolean) || 'this field');
  }
  const control = el.closest('button, a[href], input, summary, [role=button], [role=link], [role=menuitem], [role=tab], [role=option]');
  const named = control || el;
  const label = named.getAttribute('aria-label') || named.innerText || named.value || named.title || named.querySelector('img[alt]')?.alt;
  const link = !!control && control.matches('a[href]:not([role=button])') && /^https?:$/.test(control.protocol)
    && control.href.split('#')[0] !== el.ownerDocument.location.href.split('#')[0];
  const form = ['submit', 'image'].includes(control?.type) ? control.form : null;
  return describe(el, link ? 'link' : control ? 'button' : 'other', label, form ? {submits: true,
    files: [...form.querySelectorAll('input[type=file]')].flatMap((input) => [...input.files].map((file) => file.name)),
    answers: [...form.querySelectorAll('textarea')].filter((area) => area.value.trim()).length} : {});
}
