/* Shared by the browser and WeChat. Pending commands live only in memory. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RequestRecovery = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function stable(value) {
    if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
    if (value && typeof value === 'object') {
      return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + stable(value[key])).join(',') + '}';
    }
    return JSON.stringify(value);
  }

  function create(options) {
    let generation = 0, pending = null;
    const scope = () => generation + ':' + options.scope();
    const changed = () => options.onChange && options.onChange();
    function reset() { generation++; pending = null; changed(); }
    function check(context) {
      if (context !== scope()) {
        const error = Error('账号或服务已切换，请重新获取资料');
        error.stale = true;
        throw error;
      }
    }
    function current() {
      if (pending && pending.context !== scope()) { pending = null; changed(); }
      return pending;
    }
    async function request(path, data, retryCommand) {
      const context = scope();
      const body = data === undefined ? undefined : JSON.parse(JSON.stringify(data));
      const retry = body === undefined || retryCommand === true;
      for (let attempt = 0; attempt < (retry ? 2 : 1); attempt++) {
        check(context);
        try {
          const response = await options.send(path, body);
          check(context);
          if (response.status >= 400) {
            const error = Error(response.body && response.body.error || '操作失败');
            error.status = response.status;
            error.definitive = response.status < 500;
            if (response.status === 401 && options.onUnauthorized) options.onUnauthorized();
            throw error;
          }
          if (response.status < 200 || response.status >= 300 || !response.body || typeof response.body !== 'object') {
            throw Error('服务返回异常');
          }
          return response.body;
        } catch (error) {
          if (error.stale || error.definitive) throw error;
          check(context);
          if (!retry || attempt === 1) throw error;
          await (options.delay ? options.delay() : new Promise(resolve => setTimeout(resolve, 250)));
        }
      }
    }
    function command(data, confirm) {
      const saved = JSON.parse(JSON.stringify(data));
      if (!saved || typeof saved.request_id !== 'string' || !saved.request_id) {
        return Promise.reject(Error('提交缺少请求编号'));
      }
      const identity = Object.assign({}, saved);
      delete identity.request_id;
      const signature = stable(identity);
      let entry = current();
      if (entry && entry.signature !== signature) {
        const error = Error('上次提交结果待核对，请先点击“核对上次提交”');
        error.recoverable = true;
        return Promise.reject(error);
      }
      if (!entry) {
        entry = {data: saved, signature, context: scope(), committed: false, flight: null};
        pending = entry;
        changed();
      }
      if (entry.flight) return entry.flight;
      entry.flight = (async function () {
        try {
          const result = await request('/action', entry.data, true);
          check(entry.context);
          entry.committed = true;
          await confirm(result);
          check(entry.context);
          if (pending === entry) pending = null;
          changed();
          return result;
        } catch (cause) {
          // DOMException.message can be read-only; expose an ordinary Error.
          const error = Error(cause.message || '连接中断');
          for (const key of ['status', 'definitive', 'stale']) error[key] = cause[key];
          if (!error.stale && pending === entry) {
            if (error.definitive) pending = null;
            else {
              error.recoverable = true;
              error.committed = entry.committed;
              error.message = entry.committed ? '操作已保存，但资料刷新失败，请核对上次提交' : '连接中断，提交结果待核对，请核对上次提交';
            }
            changed();
          }
          throw error;
        } finally {
          entry.flight = null;
        }
      })();
      return entry.flight;
    }
    return {
      request, command, reset,
      pending() { const entry = current(); return {pending: !!entry, committed: !!(entry && entry.committed)}; },
      retry(confirm) {
        const entry = current();
        return entry ? command(entry.data, confirm) : Promise.reject(Error('没有待核对的提交'));
      }
    };
  }
  return {create};
});
