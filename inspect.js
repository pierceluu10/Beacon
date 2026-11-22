(element) => {
  // Names that mark a field as a password, card, code or identity number whatever its type and autocomplete say
  const SECRET_NAME = new RegExp('pass(word|code|phrase|port)|one[-_ ]?time|social security|(?<![a-z])(pin|otp|cvv2?|cvc2?|csc|ssn)(?![a-z])'
    + '|card[-_ ]?(number|num|no)(?![a-z])|(?<![a-z])cc[-_]?num|(verification|security|auth|authentication|2fa|mfa)[-_ ]?code', 'i');
  const CODE = /code/i;
  const PUBLIC_CODE = /(zip|postal|post|promo|coupon|discount|referral|area|country|invite)[-_ ]?code/i;
  const [CODE_LENGTH_MIN, CODE_LENGTH_MAX] = [4, 8];  // a verification code box: numeric and this short
  const READABLE = /[\p{L}\p{N}]/u;  // an icon or emoji alone names nothing
  const describe = (el, kind, label = '', extra = {}) => ({host: el.ownerDocument.location.hostname, kind,
    label: (label || '').replace(/\s+/g, ' ').trim(), submits: false, files: [], answers: 0,
    page: `${el.ownerDocument.title} ${el.ownerDocument.location.pathname}`, form: '', ...extra});
  const names = (entry) => [entry.getAttribute('aria-label'), entry.labels?.[0]?.innerText, entry.placeholder, entry.name, entry.id];
  const secret = (entry) => {
    if (!entry) return false;
    const named = names(entry).join(' ');
    return entry.type === 'password' || SECRET_NAME.test(named)
      || /(^|\s)(cc-|one-time-code|current-password)/i.test(entry.getAttribute('autocomplete') || '')
      || entry.inputMode === 'numeric' && entry.maxLength >= CODE_LENGTH_MIN && entry.maxLength <= CODE_LENGTH_MAX
        && CODE.test(named) && !PUBLIC_CODE.test(named);
  };
  const hint = (node) => node.getAttribute('aria-label') || node.getAttribute('alt') || node.getAttribute('title');
  const readable = (text) => (READABLE.test(text || '') ? text : '');
  const generated = (node) => ['::before', '::after'].map((pseudo) => node.ownerDocument.defaultView.getComputedStyle(node, pseudo)
    .content).flatMap((content) => [...content.matchAll(/"([^"]*)"/g)].map((quoted) => quoted[1]));
  // The text of the form or dialog a control sits in, with every field's names: what the step is about
  const step = (node) => {
    const container = node.form || node.closest('form, [role=form], dialog, [role=dialog]')
      || node.getRootNode().host?.closest('form, [role=form], dialog, [role=dialog]');
    return container ? [container.innerText, ...[...container.querySelectorAll('input, select, textarea')]
      .flatMap((entry) => [...names(entry), entry.getAttribute('autocomplete')])].filter(Boolean).join(' ') : '';
  };
  const labelled = (node) => (node.getAttribute('aria-labelledby') || '').split(/\s+/)
    .map((id) => node.getRootNode().getElementById(id)?.textContent).filter(Boolean).join(' ');
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
  // Playwright fills a label's control from anywhere inside the label but a button
  if (!el.closest('button, [role=button]') && secret(el.closest('label')?.control)) return describe(el, 'secret');
  const controls = 'button, a[href], input, summary, [role=button], [role=link], [role=menuitem], [role=tab], [role=option]';
  if (!el.closest(`${controls}, select, textarea`)) el = el.closest('label')?.control || el;  // a click or fill on a label lands on its control
  if (el.matches('input[type=checkbox], input[type=radio], [role=checkbox], [role=radio], [role=switch]'))
    return describe(el, 'toggle', labelled(el) || el.getAttribute('aria-label') || el.labels?.[0]?.innerText || el.innerText);
  const control = el.closest(controls);
  const entry = el.closest('textarea, select, input:not([type=submit]):not([type=button]):not([type=image]):not([type=reset]), '
    + '[contenteditable]:not([contenteditable=false]), [role=textbox], [role=searchbox], [role=combobox]');
  if (entry && (entry === control || !entry.contains(control))) {  // a button inside an editor is a button
    const entryNames = names(entry);
    const search = entry.type === 'search' || entry.getAttribute('role') === 'searchbox' || !!entry.closest('[role=search], search')
      || /(?<!re)search/i.test(entryNames.join(' '));
    return describe(el, secret(entry) ? 'secret' : search ? 'search' : 'field', entryNames.find(Boolean) || 'this field',
      {form: step(entry)});
  }
  if (element && !control && el.querySelector(`${controls}, select, textarea`))  // a click lands on whatever is at its centre
    return describe(el, 'unreadable', 'That is a container: aim at the button, link or field inside it.');
  const named = control || el;
  const label = labelled(named)  // first, as it is the name the model is shown
    || named.getAttribute('aria-label') || readable(named.innerText?.trim()) || named.value || [  // nothing readable: every other name
    hint(named), named.textContent, ...[...named.querySelectorAll('[aria-label], [alt], [title]')].map(hint),
    ...[...named.querySelectorAll('slot')].flatMap((slot) => slot.assignedNodes({flatten: true})).map((node) => node.textContent),
    ...[named, ...named.querySelectorAll('*')].flatMap(generated),
  ].filter(readable).join(' ');
  const link = !!control && control.matches('a[href]:not([role=button])') && /^https?:$/.test(control.protocol)
    && control.href.split('#')[0] !== el.ownerDocument.location.href.split('#')[0];
  const form = ['submit', 'image'].includes(control?.type) && (control.form || control.getRootNode().host?.closest('form'));
  return describe(el, link ? 'link' : control ? 'button' : 'other', label, {form: step(named), ...form ? {submits: true,
    files: [...form.querySelectorAll('input[type=file]')].flatMap((input) => [...input.files].map((file) => file.name)),
    answers: [...form.querySelectorAll('textarea')].filter((area) => area.value.trim()).length} : {}});
}
