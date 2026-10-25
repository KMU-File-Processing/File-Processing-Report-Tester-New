'use strict';

const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

async function post(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}
const get = (path) => fetch(path).then((r) => r.json());

const state = {
  config: null,
  ta: { results: [], cases: [], selected: null, selectedCase: null, job: null, sort: 'student_id', dir: 1 },
  st: { result: null, cases: [], selectedCase: null, project: null },
};

/* ------------------------------------------------------------- 초기화 */

async function init() {
  state.config = await get('/api/config');
  const student = state.config.edition === 'student';

  if (student) applyStudentEdition();

  for (const sel of [$('#ta-project'), $('#st-project')]) {
    if (!sel) continue;
    sel.innerHTML = '';
    for (const p of state.config.projects) {
      const o = el('option', null, p.label);
      o.value = p.key;
      sel.appendChild(o);
    }
  }

  const cs = $('#ta-compiler');
  if (cs) {
    cs.innerHTML = '';
    for (const c of state.config.compilers) {
      const o = el('option', null, (c.version || c.cxx) + (c.cxx17 ? '' : '  (C++17 미지원)'));
      o.value = c.cxx;
      cs.appendChild(o);
    }
  }
  if (state.config.compilers.length && state.config.compilers[0].warning) {
    notice(student ? '#st-notice' : '#ta-notice', state.config.compilers[0].warning);
  }
  if (!state.config.compilers.length) {
    const msg = 'C++ 컴파일러를 찾지 못했습니다. MinGW-w64(g++) 또는 LLVM(clang++)을 설치하고 다시 실행하세요.';
    notice(student ? '#st-notice' : '#ta-notice', msg);
    if ($('#ta-run')) $('#ta-run').disabled = true;
    $('#st-run').disabled = true;
  }
  if (!state.config.xlsx && $('#ta-export')) {
    $('#ta-export').title = 'openpyxl 이 없어 xlsx 없이 저장됩니다';
  }

  if (!student) {
    $('#tab-ta').onclick = () => switchMode('ta');
    $('#tab-student').onclick = () => switchMode('student');
    $('#ta-pick').onclick = () => choosePath('folder', $('#ta-folder'));
    $('#ta-run').onclick = runTA;
    $('#ta-export').onclick = exportResults;
    document.querySelectorAll('#ta-table th.sortable').forEach((th) => {
      th.onclick = () => {
        const key = th.dataset.sort;
        state.ta.dir = state.ta.sort === key ? -state.ta.dir : 1;
        state.ta.sort = key;
        renderTable();
      };
    });
  }

  $('#st-pick').onclick = () => choosePath('file', $('#st-file'));
  $('#st-run').onclick = runStudent;
}

/* 학생용 배포본에서는 조교 화면을 DOM에서 완전히 제거한다.
   숨기기만 하면 개발자 도구로 되살릴 수 있다. 애초에 이 배포본에는
   hidden 테스트케이스가 들어 있지 않으므로 서버도 조교 기능을 거부한다. */
function applyStudentEdition() {
  document.title = 'FP-Judge 모의 채점기';
  const sub = document.querySelector('.brand .sub');
  if (sub) sub.textContent = '화일처리 모의 채점기';
  for (const sel of ['.modes', '#view-ta']) {
    const n = document.querySelector(sel);
    if (n) n.remove();
  }
  const view = $('#view-student');
  if (view) view.classList.remove('hidden');
}

function switchMode(which) {
  const ta = which === 'ta';
  $('#tab-ta').classList.toggle('on', ta);
  $('#tab-student').classList.toggle('on', !ta);
  $('#tab-ta').setAttribute('aria-selected', String(ta));
  $('#tab-student').setAttribute('aria-selected', String(!ta));
  $('#view-ta').classList.toggle('hidden', !ta);
  $('#view-student').classList.toggle('hidden', ta);
}

function notice(sel, msg, info) {
  const n = $(sel);
  if (!msg) { n.classList.add('hidden'); return; }
  n.textContent = msg;
  n.classList.toggle('info', !!info);
  n.classList.remove('hidden');
}

/* ------------------------------------------------------------- 선택기 */

/* OS 기본 파일 대화상자(탐색기)를 먼저 쓴다.
   서버가 대화상자를 띄우지 못하는 환경(tkinter 없는 리눅스 등)에서만
   웹페이지 안의 내장 선택기로 넘어간다. */
async function choosePath(mode, input) {
  const btn = mode === 'folder' ? $('#ta-pick') : $('#st-pick');
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '선택 중…'; }
  let d;
  try {
    d = await post('/api/pick', { kind: mode, start: input.value || '' });
  } catch (e) {
    d = { error: String(e), fallback: true };
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }

  if (d && d.path) {
    input.value = d.path;
    input.dispatchEvent(new Event('change'));
    return;
  }
  if (d && d.canceled) return;          // 사용자가 취소함
  if (d && d.fallback) {                // 대화상자를 못 쓰는 환경 → 내장 선택기
    if (d.error) console.warn('파일 대화상자 실패:', d.error);
    notice(mode === 'folder' ? '#ta-notice' : '#st-notice',
           'OS 파일 선택 창을 열지 못해 내장 선택기로 엽니다. ' + (d.error || ''), true);
    openPicker(mode, (path) => { input.value = path; });
    return;
  }
  notice(mode === 'folder' ? '#ta-notice' : '#st-notice',
         (d && d.error) || '파일 선택에 실패했습니다.');
}

let pickerMode = 'folder';
let pickerDone = null;
let pickerPath = null;

async function openPicker(mode, done) {
  pickerMode = mode;
  pickerDone = done;
  $('#picker-title').textContent = mode === 'folder' ? '폴더 선택' : '소스 파일 선택';
  $('#picker-choose').classList.toggle('hidden', mode !== 'folder');
  $('#picker-hint').textContent = mode === 'folder'
    ? '폴더를 눌러 이동한 뒤 아래 버튼으로 선택하세요'
    : '.c 또는 .cpp 파일을 누르면 선택됩니다';
  $('#picker').classList.remove('hidden');
  $('#picker-close').onclick = closePicker;
  $('#picker-go').onclick = () => loadPicker($('#picker-input').value);
  $('#picker-input').onkeydown = (e) => { if (e.key === 'Enter') loadPicker($('#picker-input').value); };
  $('#picker-choose').onclick = () => { pickerDone(pickerPath); closePicker(); };
  await loadPicker(pickerPath);
}

function closePicker() { $('#picker').classList.add('hidden'); }

async function loadPicker(path) {
  const d = await post('/api/browse', { path, files: pickerMode === 'file' });
  if (d.error) { $('#picker-hint').textContent = d.error; return; }
  pickerPath = d.path;
  $('#picker-input').value = d.path;

  const roots = $('#picker-roots');
  roots.innerHTML = '';
  for (const r of d.roots) {
    const b = el('button', 'ghost small', r);
    b.onclick = () => loadPicker(r);
    roots.appendChild(b);
  }

  const list = $('#picker-list');
  list.innerHTML = '';
  if (d.parent) {
    const li = el('li');
    const b = el('button', null, '.. 상위 폴더');
    b.onclick = () => loadPicker(d.parent);
    li.appendChild(b); list.appendChild(li);
  }
  for (const name of d.dirs) {
    const li = el('li');
    const b = el('button', null, name + d.sep);
    b.onclick = () => loadPicker(d.path + (d.path.endsWith(d.sep) ? '' : d.sep) + name);
    li.appendChild(b); list.appendChild(li);
  }
  for (const name of d.files || []) {
    const li = el('li', 'file');
    const b = el('button', null, name);
    b.onclick = () => {
      pickerDone(d.path + (d.path.endsWith(d.sep) ? '' : d.sep) + name);
      closePicker();
    };
    li.appendChild(b); list.appendChild(li);
  }
}

/* ------------------------------------------------------------- 조교 채점 */

async function runTA() {
  const folder = $('#ta-folder').value.trim();
  if (!folder) { notice('#ta-notice', '제출 폴더를 먼저 선택하세요.'); return; }
  notice('#ta-notice', null);

  const res = await post('/api/grade', {
    project: $('#ta-project').value,
    mode: 'hidden',
    folder,
    compiler: $('#ta-compiler').value,
    workers: Number($('#ta-workers').value) || 4,
    check_leak: $('#ta-leak').checked,
  });
  if (res.error) { notice('#ta-notice', res.error); return; }

  state.ta.cases = res.cases;
  state.ta.job = res.job;
  state.ta.selected = null;
  $('#ta-empty').classList.add('hidden');
  $('#ta-results').classList.add('hidden');
  $('#ta-export').disabled = true;
  $('#ta-run').disabled = true;
  $('#ta-progress').classList.remove('hidden');

  poll(res.job, (job) => {
    state.ta.results = job.results;
    $('#ta-progress').classList.add('hidden');
    $('#ta-run').disabled = false;
    $('#ta-export').disabled = false;
    $('#ta-results').classList.remove('hidden');
    renderTable();
    if (job.problems && job.problems.length) {
      notice('#ta-notice',
        `채점에서 제외된 파일 ${job.problems.length}건: ` +
        job.problems.map((p) => `${p.filename} (${p.reason})`).join(' / '), true);
    }
  }, '#ta-progress', '#ta-notice', () => { $('#ta-run').disabled = false; });
}

function poll(jobId, onDone, progressSel, noticeSel, onFail) {
  const tick = async () => {
    const job = await get('/api/job/' + jobId);
    if (job.error) { notice(noticeSel, job.error); if (onFail) onFail(); return; }
    if (job.state === 'running') {
      const pct = job.total ? (job.done / job.total) * 100 : 0;
      const p = $(progressSel);
      p.querySelector('.fill').style.width = pct + '%';
      p.querySelector('.count').textContent = `${job.done} / ${job.total}`;
      setTimeout(tick, 400);
      return;
    }
    if (job.state === 'error') {
      $(progressSel).classList.add('hidden');
      notice(noticeSel, job.error || '채점 중 오류가 발생했습니다.');
      if (onFail) onFail();
      return;
    }
    onDone(job);
  };
  tick();
}

const VERDICT_CLASS = (v) =>
  v === 'AC' ? 'ac' : v === 'WA' ? 'wa' : v === 'TLE' ? 'tle' : v === 'CE' ? 'ce' : 're';

function sortKey(r, key) {
  switch (key) {
    case 'student_id': return r.student_id;
    case 'name': return r.name;
    case 'compile': return r.compile.ok ? 1 : 0;
    case 'simple': return r.score.simple_passed;
    case 'random': return r.score.random_passed;
    case 'leak': return r.leak;
    case 'score': return r.score.raw;
    default: return r.student_id;
  }
}

function renderTable() {
  const tbody = $('#ta-table tbody');
  tbody.innerHTML = '';
  const rows = [...state.ta.results].sort((a, b) => {
    const x = sortKey(a, state.ta.sort), y = sortKey(b, state.ta.sort);
    return (x > y ? 1 : x < y ? -1 : 0) * state.ta.dir;
  });

  for (const r of rows) {
    const tr = el('tr');
    tr.classList.toggle('on', state.ta.selected === r.student_id);

    tr.appendChild(el('td', 'id', r.student_id));
    tr.appendChild(el('td', null, r.name || '—'));

    const comp = el('td');
    comp.appendChild(el('span', 'tag ' + (r.compile.ok ? 'ok' : 'bad'),
      r.compile.ok ? (r.compile.werror_ok ? '성공' : '성공 · 경고') : '실패'));
    tr.appendChild(comp);

    tr.appendChild(el('td', 'num', r.score.simple));
    tr.appendChild(el('td', 'num', r.score.random));

    const leak = el('td');
    const lm = { yes: ['bad', '발생'], no: ['ok', '없음'], unknown: ['warn', '확인 불가'] }[r.leak];
    leak.appendChild(el('span', 'tag ' + lm[0], lm[1]));
    tr.appendChild(leak);

    tr.appendChild(el('td', 'num', String(r.score.raw)));

    const rib = el('td');
    const strip = el('div', 'ribbon');
    let prevKind = null;
    for (const c of r.cases) {
      if (prevKind && c.kind !== prevKind) strip.appendChild(el('span', 'gap'));
      prevKind = c.kind;
      const cell = el('i', VERDICT_CLASS(c.verdict));
      cell.title = `${c.name} · ${c.verdict}`;
      strip.appendChild(cell);
    }
    rib.appendChild(strip);
    tr.appendChild(rib);

    tr.onclick = () => {
      state.ta.selected = r.student_id;
      state.ta.selectedCase = (r.cases.find((c) => c.verdict !== 'AC') || r.cases[0] || {}).name;
      renderTable();
      renderDetail(r);
    };
    tbody.appendChild(tr);
  }

  const sel = state.ta.results.find((r) => r.student_id === state.ta.selected);
  if (sel) renderDetail(sel); else $('#ta-detail').innerHTML = '';
}

function renderDetail(r) {
  const root = $('#ta-detail');
  root.innerHTML = '';

  const list = el('div', 'caselist');
  for (const c of r.cases) {
    const b = el('button');
    b.classList.toggle('on', c.name === state.ta.selectedCase);
    b.appendChild(el('span', null, c.name));
    b.appendChild(el('span', 'v tag ' + (c.verdict === 'AC' ? 'ok' : 'bad'), c.verdict));
    b.onclick = () => { state.ta.selectedCase = c.name; renderDetail(r); };
    list.appendChild(b);
  }
  root.appendChild(list);

  const pane = el('div', 'pane');
  const c = r.cases.find((x) => x.name === state.ta.selectedCase);

  pane.appendChild(el('h3', null, `${r.student_id} ${r.name} · ${r.source}`));
  const meta = el('div', 'meta');
  meta.textContent = `표준 ${r.compile.std || '-'} · 원점수 ${r.score.raw} · 최종 ${r.score.final}`
    + (r.compile.werror_ok ? '' : ' · -Werror 경고 있음');
  pane.appendChild(meta);

  if (r.leak === 'unknown' && r.leak_reason) {
    pane.appendChild(el('h3', null, '메모리 누수 확인 불가'));
    pane.appendChild(el('pre', 'log', r.leak_reason));
  }

  if (!r.compile.ok) {
    pane.appendChild(el('h3', null, '컴파일 실패'));
    pane.appendChild(el('pre', 'log', r.compile.stderr || '(로그 없음)'));
  } else if (c && c.verdict === 'AC') {
    pane.appendChild(el('div', 'dim', `${c.name} 통과 (${c.ms}ms)`));
    if (!r.compile.werror_ok && r.compile.werror_stderr) {
      pane.appendChild(el('h3', null, '컴파일 경고'));
      pane.appendChild(el('pre', 'log', r.compile.werror_stderr));
    }
  } else if (c && c.diff) {
    pane.appendChild(el('div', 'meta',
      `${c.name} · 첫 불일치 ${c.diff.line}행 · 내 출력 ${c.diff.actual_lines}줄 / 정답 ${c.diff.expected_lines}줄`));
    pane.appendChild(diffTable(c.diff));
    const acts = el('div', 'rowactions');
    const cp = el('button', 'ghost small', '입력 복사');
    cp.onclick = () => copyCaseInput(r, c.name, cp, 'hidden');
    acts.appendChild(cp);
    pane.appendChild(acts);
  } else if (c) {
    pane.appendChild(el('h3', null, `${c.name} · ${c.verdict}`));
    pane.appendChild(el('div', 'dim',
      c.verdict === 'TLE' ? '제한 시간 안에 끝나지 않았습니다.'
        : c.verdict === 'CE' ? '컴파일에 실패해 실행하지 못했습니다.'
          : '실행 중 비정상 종료했습니다.'));
  }
  root.appendChild(pane);
}

function diffTable(diff) {
  const t = el('table', 'difftable');
  for (const row of diff.rows) {
    if (row.same) {
      const tr = el('tr');
      tr.appendChild(el('td', 'ln', String(row.line)));
      tr.appendChild(el('td', 'sig', ' '));
      tr.appendChild(el('td', null, row.actual === null ? '' : row.actual));
      t.appendChild(tr);
    } else {
      const a = el('tr', 'minus');
      a.appendChild(el('td', 'ln', String(row.line)));
      a.appendChild(el('td', 'sig', '−'));
      a.appendChild(el('td', null, row.actual === null ? '(출력 없음)' : row.actual));
      t.appendChild(a);
      const b = el('tr', 'plus');
      b.appendChild(el('td', 'ln', String(row.line)));
      b.appendChild(el('td', 'sig', '+'));
      b.appendChild(el('td', null, row.expected === null ? '(정답에 없음)' : row.expected));
      t.appendChild(b);
    }
  }
  return t;
}

async function copyCaseInput(r, caseName, btn, mode) {
  const sel = mode === 'hidden' ? $('#ta-project') : $('#st-project');
  const project = sel.value;
  const d = await post('/api/case-input', { project, mode, case: caseName });
  if (d.error) { btn.textContent = d.error; return; }
  try {
    await navigator.clipboard.writeText(d.stdin);
    btn.textContent = '복사했습니다';
    setTimeout(() => (btn.textContent = '입력 복사'), 1600);
  } catch {
    btn.textContent = '복사 실패';
  }
}

async function exportResults() {
  const btn = $('#ta-export');
  btn.disabled = true;
  btn.textContent = '저장 중';
  const d = await post('/api/export', { job: state.ta.job });
  btn.disabled = false;
  btn.textContent = '결과 저장';
  if (d.error) { notice('#ta-notice', d.error); return; }
  notice('#ta-notice', `저장했습니다: ${d.dir}`, true);
}

/* ------------------------------------------------------------- 학생 모드 */

async function runStudent() {
  const file = $('#st-file').value.trim();
  if (!file) { notice('#st-notice', '소스 파일을 먼저 선택하세요.'); return; }
  notice('#st-notice', null);

  const project = $('#st-project').value;
  const comp = $('#ta-compiler');
  const res = await post('/api/grade', {
    project, mode: 'public', single_file: file,
    compiler: comp ? comp.value : undefined,
    workers: 1, check_leak: true,
  });
  if (res.error) { notice('#st-notice', res.error); return; }

  state.st.project = project;
  $('#st-empty').classList.add('hidden');
  $('#st-result').classList.add('hidden');
  $('#st-run').disabled = true;
  $('#st-progress').classList.remove('hidden');

  poll(res.job, (job) => {
    $('#st-progress').classList.add('hidden');
    $('#st-run').disabled = false;
    state.st.result = job.results[0];
    const bad = state.st.result.cases.find((c) => c.verdict !== 'AC');
    state.st.selectedCase = bad ? bad.name : null;
    $('#st-result').classList.remove('hidden');
    renderStudent();
  }, '#st-progress', '#st-notice', () => { $('#st-run').disabled = false; });
}

function renderStudent() {
  const r = state.st.result;
  const root = $('#st-result');
  root.innerHTML = '';

  if (!r.compile.ok) {
    const line = el('div', 'verdictline');
    line.appendChild(el('div', 'big some', '컴파일 실패'));
    root.appendChild(line);
    root.appendChild(el('div', 'meta dim', '아래 오류를 먼저 해결하세요.'));
    root.appendChild(el('pre', 'log', r.compile.stderr || '(로그 없음)'));
    return;
  }

  const passed = r.cases.filter((c) => c.verdict === 'AC').length;
  const total = r.cases.length;

  const line = el('div', 'verdictline');
  line.appendChild(el('div', 'big ' + (passed === total ? 'allpass' : 'some'),
    `${passed} / ${total}`));

  const w = el('div', 'item');
  w.innerHTML = '컴파일 경고 <b></b>';
  w.querySelector('b').textContent = r.compile.werror_ok ? '없음' : '있음';
  line.appendChild(w);

  const lk = el('div', 'item');
  lk.innerHTML = '메모리 누수 <b></b>';
  lk.querySelector('b').textContent =
    { yes: '발생', no: '없음', unknown: '확인 불가' }[r.leak];
  line.appendChild(lk);
  root.appendChild(line);

  if (r.leak === 'unknown' && r.leak_reason) {
    root.appendChild(el('h3', null, '메모리 누수 확인 불가'));
    root.appendChild(el('pre', 'log', r.leak_reason));
  }

  const grid = el('div', 'casegrid');
  for (const c of r.cases) {
    const b = el('button', (c.verdict === 'AC' ? 'ac' : 'bad')
      + (c.name === state.st.selectedCase ? ' on' : ''), c.name);
    b.title = c.verdict;
    b.onclick = () => { state.st.selectedCase = c.name; renderStudent(); };
    grid.appendChild(b);
  }
  root.appendChild(grid);

  if (!r.compile.werror_ok && r.compile.werror_stderr) {
    root.appendChild(el('h3', null, '컴파일 경고 (-Wall -Wextra -Werror)'));
    root.appendChild(el('pre', 'log', r.compile.werror_stderr));
  }

  const c = r.cases.find((x) => x.name === state.st.selectedCase);
  if (!c) {
    if (passed === total) root.appendChild(el('p', 'dim', '공개 테스트케이스를 모두 통과했습니다.'));
    return;
  }
  if (c.verdict === 'AC') {
    root.appendChild(el('p', 'dim', `${c.name} 통과 (${c.ms}ms)`));
    return;
  }

  root.appendChild(el('h3', null, `${c.name} · ${c.verdict}`));
  if (c.diff) {
    root.appendChild(el('div', 'meta',
      `첫 불일치 ${c.diff.line}행 · 내 출력 ${c.diff.actual_lines}줄 / 정답 ${c.diff.expected_lines}줄`));
    root.appendChild(diffTable(c.diff));
  } else {
    root.appendChild(el('div', 'dim',
      c.verdict === 'TLE' ? '제한 시간 안에 끝나지 않았습니다. 무한 루프를 확인하세요.'
        : '실행 중 비정상 종료했습니다. 널 포인터 접근이나 잘못된 메모리 해제를 확인하세요.'));
  }
  const acts = el('div', 'rowactions');
  const cp = el('button', 'ghost small', '입력 복사');
  cp.onclick = () => copyCaseInput(r, c.name, cp, 'public');
  acts.appendChild(cp);
  root.appendChild(acts);
}

init();
