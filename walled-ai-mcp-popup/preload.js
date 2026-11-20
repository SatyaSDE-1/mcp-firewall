
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('firewall', {
  onState: (cb) => ipcRenderer.on('state', (_e, s) => cb(s)),
  decide: (requestId, decision) =>
    ipcRenderer.send('decision', { requestId, decision }),
});