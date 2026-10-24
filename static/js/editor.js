import { h } from './util.js';

const KW = new Set(('select from where group by having order limit offset join left right full inner outer cross natural on using as and or not in is null like glob between ' +
  'exists case when then else end distinct all union intersect except with recursive over partition rows range groups unbounded preceding following current row ' +
  'window asc desc nulls first last filter exclude ties others values true false collate').split(' '));
const RE = /(--[^\n]*|\/\*[\s\S]*?(?:\*\/|$))|('(?:[^']|'')*'?)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z_0-9]*)(\s*\()?/g;

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function segments(text) {
  const segs = []; let last = 0, m;
  RE.lastIndex = 0;
  while ((m = RE.exec(text))) {
    if (m.index > last) segs.push([last, m.index, '']);
    let cls = '', end = m.index + m[0].length;
    if (m[1] !== undefined) cls = 'c';
    else if (m[2] !== undefined) cls = 's';
    else if (m[3] !== undefined) cls = 'n';
    else if (m[4] !== undefined) {
      if (KW.has(m[4].toLowerCase()) && !m[5]) cls = 'k';
      else if (m[5]) { cls = 'f'; end = m.index + m[4].length; }
    }
    segs.push([m.index, end, cls]);
    last = end;
  }
  if (last < text.length) segs.push([last, text.length, '']);
  return segs;
}

export class Editor {
  constructor(root, { onInput, onCaret, onRun, onFormat }) {
    this.mark = null;
    this.pre = h('pre', { 'aria-hidden': 'true' });
    this.ta = h('textarea', { spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off', 'aria-label': 'SQL query' });
    root.append(this.pre, this.ta);
    this.cb = { onInput, onCaret, onRun, onFormat };
    this.ta.addEventListener('input', () => { this.paint(); onInput(this.ta.value); onCaret(this.caret()); });
    this.ta.addEventListener('scroll', () => { this.pre.scrollTop = this.ta.scrollTop; this.pre.scrollLeft = this.ta.scrollLeft; });
    let lastCaret = -1;
    const caretCheck = () => { const c = this.caret(); if (c !== lastCaret) { lastCaret = c; onCaret(c); } };
    this.ta.addEventListener('keyup', caretCheck);
    this.ta.addEventListener('mouseup', caretCheck);
    this.ta.addEventListener('click', caretCheck);
    this.ta.addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); onRun(); return; }
      if (e.shiftKey && e.altKey && (e.key.toLowerCase() === 'f' || e.code === 'KeyF')) { e.preventDefault(); onFormat && onFormat(); return; }
      if (e.key === 'Tab' && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        document.execCommand('insertText', false, '  ');
      }
    });
  }
  get value() { return this.ta.value; }
  set value(v) { this.ta.value = v; this.paint(); }
  caret() { return this.ta.selectionStart; }
  setText(text, caret) {
    this.ta.value = text;
    if (caret != null) this.ta.setSelectionRange(caret, caret);
    this.paint(); this.cb.onInput(text); this.cb.onCaret(this.caret());
  }
  set readOnly(v) { this.ta.readOnly = v; }
  focus() { this.ta.focus(); }
  setMark(span) { this.mark = span; this.paint(); }
  replace(span, text) {
    const v = this.ta.value;
    this.ta.value = v.slice(0, span[0]) + text + v.slice(span[1]);
    this.ta.setSelectionRange(span[0] + text.length, span[0] + text.length);
    this.paint();
    this.cb.onInput(this.ta.value);
  }
  paint() {
    const t = this.ta.value, segs = segments(t), mk = this.mark;
    let out = '';
    for (const [a, b, cls] of segs) {
      const cuts = [a, b];
      if (mk) for (const c of mk) if (c > a && c < b) cuts.push(c);
      cuts.sort((x, y) => x - y);
      for (let i = 0; i < cuts.length - 1; i++) {
        const s = cuts[i], e = cuts[i + 1];
        if (s === e) continue;
        const inMk = mk && s >= mk[0] && e <= mk[1];
        const c = [cls, inMk ? 'mk' : ''].filter(Boolean).join(' ');
        const txt = esc(t.slice(s, e));
        out += c ? `<span class="${c}">${txt}</span>` : txt;
      }
    }
    this.pre.innerHTML = out + '\n';
  }
}
