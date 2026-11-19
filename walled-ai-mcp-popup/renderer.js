// /Users/anurag/walled-ai-mcp-popup/renderer.js
const $ = (id) => document.getElementById(id);
let currentId = null;

window.firewall.onState(({ current, pending }) => {
  const isNew = current.id !== currentId;
  currentId = current.id;

  // textContent only: request data comes from untrusted tools, never innerHTML.
  $('source').textContent = current.source;
  $('server').textContent = current.serverName;
  $('method').textContent = current.method;
  $('tool').textContent = current.toolName ?? '-';
  $('pending').textContent = pending > 1 ? `${pending - 1} more waiting` : '';

  let params;
  try {
    params = JSON.stringify(current.params ?? {}, null, 2);
  } catch {
    params = String(current.params);
  }
  if (params.length > 4000) params = params.slice(0, 4000) + '\n... (truncated)';
  $('params').textContent = params;

  if (isNew) {
    // Brief lock so a click meant for the window underneath can't approve this.
    $('allow').disabled = true;
    setTimeout(() => ($('allow').disabled = false), 600);
  }
  $('block').focus();
});

$('allow').addEventListener('click', () => window.firewall.decide(currentId, 'allow'));
$('block').addEventListener('click', () => window.firewall.decide(currentId, 'block'));
// Esc blocks. There is deliberately no keyboard shortcut for Allow.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') window.firewall.decide(currentId, 'block');
});