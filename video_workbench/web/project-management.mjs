/** Reversible project removal. A lost write response is reconciled with reads. */
export function createProjectManagement({ document, request, getContext, onChange,
  onRemoved, onRestored, onError, onSuccess, onPending }) {
  const $ = id => document.getElementById(id);
  let view = 'active', trash = [], loaded = false, loading = false, readSequence = 0;
  let intent = null, writing = false, uncertain = null;
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const error = message => onError({ status: 422, message, lifecycleMessage: message });
  const busyReason = () => {
    const context = getContext();
    if (writing || uncertain) return '项目管理操作尚未确认，请先重新读取状态。';
    if (!context.project) return '请先打开要删除的项目。';
    if (context.unsaved) return '请先保存或撤销尚未保存的修改，再删除项目。';
    if (context.busy) return '项目仍有制作任务或待确认的提交，请等待结束后再删除。';
    return '';
  };

  function render() {
    const context = getContext();
    $('show-active-projects').setAttribute('aria-pressed', String(view === 'active'));
    $('show-project-trash').setAttribute('aria-pressed', String(view === 'trash'));
    $('project-list-label').textContent = view === 'trash' ? '回收站' : '项目';
    $('project-list').setAttribute('aria-label', view === 'trash' ? '回收站项目列表' : '项目列表');
    $('project-trash-note').hidden = view !== 'trash';
    $('reload-project-trash').hidden = view !== 'trash';
    $('reload-project-trash').disabled = loading || writing;
    const reason = busyReason();
    $('delete-project').disabled = Boolean(reason);
    $('delete-project').title = reason || '将当前项目移到回收站，可随时恢复';
    $('delete-project-note').textContent = context.project && reason ? reason : '';
    $('delete-project-note').hidden = !context.project || !reason;
    $('confirm-delete-project').disabled = writing || Boolean(uncertain) || !intent;
    $('confirm-delete-project').textContent = writing ? '正在删除…' : '移到回收站';
    $('cancel-delete-project').disabled = writing;
    $('close-delete-project').disabled = writing;
    $('reload-project-management').hidden = !uncertain;
    $('project-delete-status').textContent = uncertain
      ? '提交结果尚未确认。请重新读取状态，系统不会重复提交删除或恢复。' : '';
    $('project-delete-status').hidden = !uncertain;
    if (intent && !writing && !uncertain
        && (intent.projectId !== context.project?.id || intent.epoch !== context.epoch)) {
      intent = null; $('delete-project-dialog').close();
    }
    if (view !== 'trash') return false;
    $('project-count').textContent = loading && !loaded ? '—' : trash.length;
    const fragment = document.createDocumentFragment();
    for (const project of trash) {
      const row = node('div', undefined, 'project-trash-item');
      const label = node('div', undefined, 'project-item-text');
      label.append(node('strong', project.title), node('small', `${project.scene_count ?? 0} 个镜头 · 可恢复`));
      const restore = node('button', '恢复并打开', 'text-button');
      restore.type = 'button'; restore.dataset.restoreProjectId = project.id;
      restore.disabled = writing || Boolean(uncertain);
      restore.addEventListener('click', () => restoreProject(project));
      row.append(label, restore); fragment.append(row);
    }
    if (!trash.length) fragment.append(node('p', loading ? '正在读取回收站…' : '回收站为空。', 'sidebar-empty'));
    $('project-list').replaceChildren(fragment);
    return true;
  }

  async function loadTrash() {
    const sequence = ++readSequence;
    loading = true; onChange();
    try {
      const result = await request('/api/projects?deleted=true');
      if (sequence !== readSequence) return;
      trash = result.projects ?? []; loaded = true;
    } catch (failure) { if (sequence === readSequence) onError(failure); }
    finally { if (sequence === readSequence) { loading = false; onChange(); } }
  }

  function openDelete() {
    const reason = busyReason();
    if (reason) { error(reason); return; }
    const context = getContext();
    intent = { action: 'trash', projectId: context.project.id,
      revision: context.project.revision, epoch: context.epoch };
    $('delete-project-name').textContent = context.project.title;
    $('delete-project-dialog').showModal(); onChange();
  }
  function closeDelete() {
    if (writing) return;
    intent = null; $('delete-project-dialog').close(); onChange();
  }

  async function finish(operation) {
    uncertain = null;
    if (operation.action === 'trash') {
      intent = null; $('delete-project-dialog').close();
      await onRemoved(operation.projectId, operation.epoch);
      onSuccess('项目已移到回收站。画面、录音和制作记录已保留，可在左侧回收站恢复。');
    } else {
      view = 'active';
      await onRestored(operation.projectId, operation.epoch);
      onSuccess('项目已恢复，原有画面、录音和制作记录仍在。');
    }
    loaded = false;
  }

  async function reconcile(operation) {
    const [activeResult, trashResult] = await Promise.all([
      request('/api/projects'), request('/api/projects?deleted=true'),
    ]);
    trash = trashResult.projects ?? []; loaded = true;
    const expected = operation.action === 'trash' ? trash : activeResult.projects ?? [];
    return expected.some(project => project.id === operation.projectId);
  }

  async function mutate(operation) {
    if (writing || uncertain) return;
    writing = true; onPending?.(operation.projectId, true); onChange();
    let accepted = false;
    try {
      await request(`/api/projects/${encodeURIComponent(operation.projectId)}/${operation.action}`,
        { method: 'POST', body: { expected_revision: operation.revision } });
      accepted = true;
    } catch (failure) {
      // A transport failure can follow a committed write. Do not retry a POST.
      if (!Number.isInteger(failure?.status) || failure.status >= 500) {
        uncertain = operation;
        try { accepted = await reconcile(operation); }
        catch { /* Preserve the unknown outcome and expose read-only recovery. */ }
        if (!accepted) error('项目管理提交结果尚未确认，请点击“重新读取项目状态”；不会自动重复提交。');
      } else {
        intent = null; $('delete-project-dialog').close(); onError(failure);
      }
    }
    try {
      if (accepted) await finish(operation);
    } catch (failure) { onError(failure); }
    finally { writing = false; onPending?.(operation.projectId, Boolean(uncertain)); onChange(); }
  }

  async function confirmDelete() {
    if (!intent || writing || uncertain) return;
    const context = getContext();
    if (context.project?.id !== intent.projectId || context.epoch !== intent.epoch) { closeDelete(); return; }
    const reason = busyReason();
    if (reason) { error(reason); return; }
    await mutate({ ...intent });
  }
  async function restoreProject(project) {
    if (writing || uncertain) return;
    const context = getContext();
    if (context.unsaved) { error('请先保存或撤销当前项目尚未保存的修改，再恢复并打开项目。'); return; }
    await mutate({ action: 'restore', projectId: project.id, revision: project.revision, epoch: context.epoch });
  }
  async function recover() {
    if (!uncertain || writing) return;
    const operation = uncertain;
    writing = true; onPending?.(operation.projectId, true); onChange();
    try {
      if (await reconcile(operation)) await finish(operation);
      else error('暂未确认项目状态，请稍后重新读取；不会重复提交。');
    } catch (failure) { onError(failure); }
    finally { writing = false; onPending?.(operation.projectId, Boolean(uncertain)); onChange(); }
  }

  $('delete-project').addEventListener('click', openDelete);
  $('confirm-delete-project').addEventListener('click', confirmDelete);
  for (const id of ['cancel-delete-project', 'close-delete-project']) $(id).addEventListener('click', closeDelete);
  $('delete-project-dialog').addEventListener('cancel', event => {
    if (writing) event.preventDefault(); else intent = null;
  });
  $('show-active-projects').addEventListener('click', () => { view = 'active'; onChange(); });
  $('show-project-trash').addEventListener('click', async () => {
    view = 'trash'; onChange(); await loadTrash();
  });
  $('reload-project-trash').addEventListener('click', async () => {
    if (uncertain) await recover(); else await loadTrash();
  });
  $('reload-project-management').addEventListener('click', recover);
  return { render, showActive() { view = 'active'; onChange(); },
    async refresh() { if (view === 'trash') await loadTrash(); },
    hasUnresolvedOperation() { return writing || Boolean(uncertain); } };
}
