App({
  globalData: { apiBase: 'http://127.0.0.1:8080', token: '' },
  request(path, method = 'GET', data = {}) {
    return new Promise((resolve, reject) => {
      wx.request({
        url: this.globalData.apiBase + path,
        method,
        data,
        header: { Authorization: 'Bearer ' + this.globalData.token },
        success: ({ statusCode, data: body }) => statusCode >= 200 && statusCode < 300 ? resolve(body) : reject(new Error(body.error || 'request_failed')),
        fail: reject
      });
    });
  }
});
