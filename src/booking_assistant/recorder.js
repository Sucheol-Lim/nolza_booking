(() => {
  // iframe(결제창 등)과 결제사 화면(팝업 포함)은 녹화하지 않는다. 목록은 detect.PAYMENT_URL_MARKERS.
  const PAYMENT = new RegExp(__BA_PAYMENT_MARKERS__, 'i');
  if (window.top !== window || window.__baRecorderInstalled) return;
  if (PAYMENT.test(location.href)) return;
  window.__baRecorderInstalled = true;

  const send = (ev) => { try { window.__baRecord(ev); } catch (e) {} };
  const TEXT_TYPES = ['text', 'search', 'tel', 'email', 'url', 'number', 'date', 'time',
    'month', 'week', 'datetime-local', 'range', 'color', 'file', 'password', ''];
  let finalMode = false;
  let badge = null;

  // 카드번호·비밀번호·PIN으로 보이는 입력칸(id/name 기준)
  const SENSITIVE_NAME = /card|cvc|cvv|pwd|passw|(^|[^a-z])pin/i;
  const sensitiveName = (s) => !!s && (SENSITIVE_NAME.test(s) || /[a-z]Pin/.test(s));
  const isSensitive = (el) => {
    if (!el || el.nodeType !== 1) return false;
    const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
    return (el.tagName === 'INPUT' && el.type === 'password') || ac.startsWith('cc-')
      || sensitiveName(el.id) || sensitiveName(el.getAttribute('name'));
  };

  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

  // 글자형 보안문자 화면(detect.py와 같은 기준)에서는 입력·클릭을 기록하지 않는다.
  // 체크박스형 위젯(reCAPTCHA·hCaptcha·Turnstile)은 iframe 안이라 원래 기록되지 않으므로,
  // 위젯이 있다는 이유로 같은 화면의 예약 입력까지 버리지 않는다.
  const CAPTCHA_TEXT = /보안문자|자동입력 ?방지|로봇이 아닙니다|(?<![a-z])captcha(?![a-z])/i;
  const onCaptchaPage = () => {
    if (/captcha|turnstile/i.test(location.href)) return true;
    try {
      if (CAPTCHA_TEXT.test(document.body ? document.body.innerText : '')) return true;
    } catch (e) {}
    return false;
  };
  const record = (ev) => { if (!onCaptchaPage()) send(ev); };

  const roleOf = (el) => {
    const r = el.getAttribute('role');
    if (r) return r;
    const tag = el.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (['button', 'submit', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      return 'textbox';
    }
    return null;
  };

  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria);
    if (el.labels && el.labels.length) {
      return clean(el.labels[0].innerText || el.labels[0].textContent);
    }
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' && ['button', 'submit', 'reset'].includes(el.type) && el.value) {
      return clean(el.value);
    }
    const text = clean(el.innerText);
    if (text) return text.slice(0, 80);
    return clean(el.getAttribute('placeholder') || el.getAttribute('title')
      || el.getAttribute('alt')) || null;
  };

  const cssEsc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/([^\w-])/g, '\\$1');

  const nthPath = (el) => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      const tag = cur.tagName.toLowerCase();
      let idx = 1;
      for (let s = cur.previousElementSibling; s; s = s.previousElementSibling) {
        if (s.tagName === cur.tagName) idx++;
      }
      parts.unshift(tag + ':nth-of-type(' + idx + ')');
      if (tag === 'body') break;
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };

  const describe = (el) => {
    const tag = el.tagName.toLowerCase();
    const text = clean(el.innerText).slice(0, 80);
    const css = [];
    if (el.id) css.push('#' + cssEsc(el.id));
    const name = el.getAttribute('name');
    if (name) css.push('[name="' + name.replace(/"/g, '\\"') + '"]');
    if (text && !text.includes('"')) css.push(tag + ':has-text("' + text + '")');
    css.push(nthPath(el));
    return { role: roleOf(el), name: nameOf(el), text: text || null, css, tag };
  };

  const setBadge = (on) => {
    if (on) {
      if (!badge) {
        badge = document.createElement('div');
        badge.textContent = '다음 클릭 = 최종 제출 (녹화 종료)';
        badge.style.cssText = 'position:fixed;top:8px;right:8px;z-index:2147483647;'
          + 'background:#c62828;color:#fff;padding:6px 10px;font:14px sans-serif;'
          + 'border-radius:4px;pointer-events:none';
      }
      (document.body || document.documentElement).appendChild(badge);
    } else if (badge && badge.parentNode) {
      badge.parentNode.removeChild(badge);
    }
  };

  window.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey && e.shiftKey)) return;
    if (e.code === 'KeyF') {
      e.preventDefault();
      finalMode = !finalMode;
      setBadge(finalMode);
    } else if (e.code === 'KeyS') {
      e.preventDefault();
      send({ type: 'stop' });
    }
  }, true);

  window.addEventListener('click', (e) => {
    let el = e.target;
    if (!el || el.nodeType !== 1) return;
    const interactive = el.closest('button,a,input,select,textarea,label,[role=button],[role=link]');
    if (interactive) el = interactive;
    if (isSensitive(el)) return;

    if (finalMode) {
      finalMode = false;
      setBadge(false);
      e.preventDefault();
      e.stopImmediatePropagation();
      send({ type: 'final_click', target: describe(el), url: location.href });
      return;
    }

    const tag = el.tagName.toLowerCase();
    // 입력창·select 클릭은 input/change 이벤트가 대신 기록한다.
    if (tag === 'textarea' || tag === 'select') return;
    if (tag === 'input' && TEXT_TYPES.includes((el.type || '').toLowerCase())) return;
    // 라벨 클릭은 연결된 컨트롤의 합성 click이 기록하므로 중복을 피한다.
    if (tag === 'label' && el.control) return;
    record({ type: 'click', target: describe(el), url: location.href });
  }, true);

  window.addEventListener('input', (e) => {
    const el = e.target;
    if (!el || el.nodeType !== 1 || isSensitive(el)) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') return;
    if (tag === 'input' && ['radio', 'checkbox'].includes(el.type)) return;
    if (tag !== 'input' && tag !== 'textarea' && !el.isContentEditable) return;
    record({ type: 'input', target: describe(el), value: el.value, url: location.href });
  }, true);

  window.addEventListener('change', (e) => {
    const el = e.target;
    if (!el || el.nodeType !== 1 || isSensitive(el) || el.tagName !== 'SELECT') return;
    record({ type: 'change', target: describe(el), value: el.value, url: location.href });
  }, true);

  if (!location.href.startsWith('about:')) {
    // 문서가 다 그려진 뒤에야 보안문자 문구를 볼 수 있다.
    const nav = () => record({ type: 'nav', url: location.href });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', nav);
    else nav();
  }
})();
