(element) => {
  const describe = (el, kind, label = '', extra = {}) => ({host: el.ownerDocument.location.hostname, kind,
    label: (label || '').replace(/\s+/g, ' ').trim(), submits: false, files: [], answers: 0, ...extra});
  const names = (entry) => [entry.getAttribute('aria-label'), entry.labels?.[0]?.innerText, entry.placeholder, entry.name, entry.id];
  const searches = (entry) => entry.type === 'search' || entry.getAttribute('role') === 'searchbox'
    || !!entry.closest('[role=search], search') || /(?<!re)search/i.test(names(entry).join(' '));
  const hint = (node) => node.getAttribute('aria-label') || node.getAttribute('alt') || node.getAttribute('title');
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
  const controls = 'button, a[href], input, summary, [role=button], [role=link], [role=menuitem], [role=tab], [role=option]';
  if (!el.closest(`${controls}, select, textarea`)) el = el.closest('label')?.control || el;  // a click or fill on a label lands on its control
  if (el.matches('input[type=checkbox], input[type=radio], [role=checkbox], [role=radio], [role=switch]')) return describe(el, 'toggle');
  const control = el.closest(controls);
  const entry = el.closest('textarea, select, input:not([type=submit]):not([type=button]):not([type=image]):not([type=reset]):not([type=file]), '
    + '[contenteditable]:not([contenteditable=false]), [role=textbox], [role=searchbox], [role=combobox]');
  if (entry && (entry === control || !entry.contains(control))) {  // a button inside an editor is a button
    const secret = entry.type === 'password'
      || /(^|\s)(cc-|one-time-code|current-password|new-password)/i.test(entry.getAttribute('autocomplete') || '');
    return describe(el, secret ? 'secret' : searches(entry) ? 'search' : 'field', names(entry).find(Boolean) || 'this field');
  }
  const named = control || el;
  const label = named.getAttribute('aria-label') || named.innerText?.trim() || named.value || [  // nothing visible: every other name
    ...(named.getAttribute('aria-labelledby') || '').split(/\s+/).map((id) => named.getRootNode().getElementById(id)?.textContent),
    hint(named), named.textContent, ...[...named.querySelectorAll('[aria-label], [alt], [title]')].map(hint),
    ...[...named.querySelectorAll('slot')].flatMap((slot) => slot.assignedNodes({flatten: true})).map((node) => node.textContent),
  ].filter(Boolean).join(' ');
  const link = !!control && control.matches('a[href]:not([role=button])') && /^https?:$/.test(control.protocol)
    && control.href.split('#')[0] !== el.ownerDocument.location.href.split('#')[0];
  const form = ['submit', 'image'].includes(control?.type) && control.form;
  return describe(el, link ? 'link' : control ? 'button' : 'other', label, form && ![...form.elements].some(searches) ? {submits: true,
    files: [...form.querySelectorAll('input[type=file]')].flatMap((input) => [...input.files].map((file) => file.name)),
    answers: [...form.querySelectorAll('textarea')].filter((area) => area.value.trim()).length} : {});
}
