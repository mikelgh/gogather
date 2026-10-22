/* 新手引导：3 步遮罩式 tour
 *
 * 首次登录且还没有方案时自动出现；之后可从用户菜单「新手引导」重看。
 * 移动端侧边栏是抽屉，遮罩挖孔会挖到屏幕外，所以窄屏退化成底部卡片。
 */
(function () {
  const DONE_KEY = 'juxing_tour_done';
  const MOBILE_MAX = 640;
  const PAD = 8;

  const STEPS = [
    {
      selector: '#sidebar',
      place: 'right',
      title: () => t('tour.s1Title', '所有方案都在这里'),
      desc: () => t('tour.s1Desc', '每次生成的方案都会存进左侧列表，点一下就能切回来。手机上从左上角菜单打开。'),
    },
    {
      selector: '#chatInput',
      place: 'top',
      title: () => t('tour.s2Title', '用一句话说清这次活动'),
      desc: () => t('tour.s2Desc', '人数、日期、出发地、预算说得越具体，AI 给的方案越贴近实际。它会先给大纲，你确认后再生成完整行程。'),
    },
    {
      selector: '#sceneActions',
      place: 'top',
      title: () => t('tour.s3Title', '不知道怎么开头？'),
      desc: () => t('tour.s3Desc', '点这些预设场景快速起个草稿，再补上人数和日期就行。下面三个筛选可以设定预算档次和交通方式。'),
    },
  ];

  let idx = 0;
  let overlay = null;

  const isMobile = () => window.innerWidth <= MOBILE_MAX;

  function done() {
    try { localStorage.setItem(DONE_KEY, '1'); } catch (e) { /* 隐私模式下写不了，忽略 */ }
  }
  function hasDone() {
    try { return localStorage.getItem(DONE_KEY) === '1'; } catch (e) { return false; }
  }

  function build() {
    const el = document.createElement('div');
    el.className = 'tour-overlay';
    el.innerHTML = `
      <div class="tour-hole"></div>
      <div class="tour-tip" role="dialog" aria-modal="true">
        <div class="tour-step-count"></div>
        <h3 class="tour-tip-title"></h3>
        <p class="tour-tip-desc"></p>
        <div class="tour-foot">
          <button class="tour-skip" type="button" data-copy="tour.skip">跳过</button>
          <div class="tour-nav">
            <button class="tour-prev" type="button">上一步</button>
            <button class="tour-next" type="button">下一步</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(el);
    el.querySelector('.tour-skip').onclick = stop;
    el.querySelector('.tour-prev').onclick = () => go(idx - 1);
    el.querySelector('.tour-next').onclick = () => go(idx + 1);
    return el;
  }

  function onKey(e) {
    if (e.key === 'Escape') { e.stopPropagation(); stop(); }
    else if (e.key === 'ArrowRight') go(idx + 1);
    else if (e.key === 'ArrowLeft') go(idx - 1);
    else if (e.key === 'Enter') go(idx + 1);
  }

  function go(n) {
    if (n < 0) return;
    if (n >= STEPS.length) { stop(); return; }
    idx = n;
    render();
  }

  function render() {
    const step = STEPS[idx];
    const tip = overlay.querySelector('.tour-tip');
    tip.querySelector('.tour-step-count').textContent = `${idx + 1} / ${STEPS.length}`;
    tip.querySelector('.tour-tip-title').textContent = step.title();
    tip.querySelector('.tour-tip-desc').textContent = step.desc();
    tip.querySelector('.tour-prev').style.visibility = idx === 0 ? 'hidden' : 'visible';
    tip.querySelector('.tour-next').textContent =
      idx === STEPS.length - 1 ? t('tour.done', '开始规划') : t('tour.next', '下一步');

    const el = document.querySelector(step.selector);
    // 元素还没渲染出来就跳过这一步，别让遮罩卡住页面
    if (!el) { go(idx + 1); return; }

    overlay.classList.toggle('tour-sheet', isMobile());
    if (isMobile()) {
      overlay.querySelector('.tour-hole').style.display = 'none';
      return;
    }

    const hole = overlay.querySelector('.tour-hole');
    hole.style.display = '';
    const r = el.getBoundingClientRect();
    hole.style.left = `${r.left - PAD}px`;
    hole.style.top = `${r.top - PAD}px`;
    hole.style.width = `${r.width + PAD * 2}px`;
    hole.style.height = `${r.height + PAD * 2}px`;

    const vw = window.innerWidth, vh = window.innerHeight;
    const th = tip.offsetHeight, tw = tip.offsetWidth, M = 16;
    let top, left;
    if (step.place === 'top') {
      top = r.top - th - 14;
      left = Math.min(Math.max(M, r.left + r.width / 2 - tw / 2), vw - tw - M);
      if (top < M) top = r.bottom + 14;
    } else {
      top = Math.min(Math.max(M, r.top + r.height / 2 - th / 2), vh - th - M);
      left = r.right + 14;
      if (left + tw > vw - M) left = r.left - tw - 14;
      if (left < M) { left = M; top = Math.min(r.bottom + 14, vh - th - M); }
    }
    tip.style.top = `${top}px`;
    tip.style.left = `${left}px`;
  }

  function stop() {
    done();
    if (overlay) { overlay.remove(); overlay = null; }
    window.removeEventListener('resize', onResize);
    document.removeEventListener('keydown', onKey, true);
  }

  function onResize() {
    if (!overlay) return;
    overlay.classList.toggle('tour-sheet', isMobile());
    render();
  }

  function start(fromStep = 0) {
    if (overlay) return;
    overlay = build();
    idx = fromStep;
    render();
    window.addEventListener('resize', onResize);
    document.addEventListener('keydown', onKey, true);
  }

  window.startTour = start;
  window.tourCompleted = hasDone;
})();