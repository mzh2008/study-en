const app = getApp();
Page({
  data: { track: 'development', adult: false, consent: false, course: null, error: '', loading: false },
  chooseTrack(event) { this.setData({ track: Number(event.detail.value) === 0 ? 'development' : 'support' }); },
  changeAdult(event) { this.setData({ adult: event.detail.value }); },
  changeConsent(event) { this.setData({ consent: event.detail.value }); },
  async start() {
    if (!this.data.adult || !this.data.consent) {
      this.setData({ error: '仅供已满18岁且同意隔离演示说明的人员使用。' });
      return;
    }
    this.setData({ loading: true, error: '' });
    try {
      // Fixture identity is local-only. WeChat code2Session is intentionally not wired.
      const session = await app.request('/v1/auth/fixture-login', 'POST', { subject: 'fixture:mini-learner' });
      app.globalData.token = session.token;
      await app.request('/v1/onboarding', 'POST', { adult: true, consentVersion: 'sandbox-v1', track: this.data.track, goalMinutes: 5 });
      await this.refresh();
    } catch (error) { this.setData({ error: error.message }); }
    finally { this.setData({ loading: false }); }
  },
  async refresh() {
    try { this.setData({ course: await app.request('/v1/course'), error: '' }); }
    catch (error) { this.setData({ error: error.message }); }
  },
  async withdraw() {
    try {
      await app.request('/v1/privacy/withdraw', 'POST');
      app.globalData.token = '';
      this.setData({ course: null, consent: false, error: '同意已撤回；训练入口已关闭。' });
    } catch (error) { this.setData({ error: error.message }); }
  }
});
