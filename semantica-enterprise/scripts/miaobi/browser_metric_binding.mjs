/** Live browser acceptance; only the isolated metric fixture is changed. */
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
const root = resolve(import.meta.dirname, '../..');
const require = createRequire(resolve(root, 'apps/miaobi-web/package.json'));
const { chromium, expect } = require('@playwright/test');
const base = (process.env.MIAOBI_DEMO_URL || 'http://localhost:8080').replace(/\/$/, '');
const projectName = '妙笔·指标联动独立验收（模拟数据）';
const articleTitle = '搜救资源指标联动演练稿（模拟数据）';
const browser = await chromium.launch({ headless: true, executablePath: chromium.executablePath() });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
try {
  await page.goto(base + '/', { waitUntil: 'domcontentloaded', timeout: 45000 });
  const password = process.env.MIAOBI_DEMO_PASSWORD || execFileSync('docker',
    ['compose', 'exec', '-T', 'api', 'python', '-c', 'import os; print(os.environ["BOOTSTRAP_ADMIN_PASSWORD"])'],
    { cwd: root, encoding: 'utf8' }).trim();
  await page.locator('[name=username]').fill(process.env.MIAOBI_DEMO_USERNAME || 'admin');
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('#logout')).toBeVisible({ timeout: 30000 });
  errors.length = 0;
  await page.goto(base + '/miaobi/', { waitUntil: 'domcontentloaded', timeout: 45000 });
  const projectSelect = page.getByLabel('选择项目');
  await expect(projectSelect).toBeVisible({ timeout: 45000 });
  await projectSelect.selectOption({ label: projectName });
  await page.locator('.sidebar').getByRole('button', { name: '报告编辑', exact: true }).click();
  const articleSelect = page.locator('.document-switcher select');
  await expect(articleSelect).toBeVisible({ timeout: 45000 });
  await articleSelect.selectOption({ label: articleTitle });
  await expect(page.locator('[data-slate-editor]')).toContainText('搜救人员缺口为180人', { timeout: 45000 });
  const articleId = await page.evaluate(() => sessionStorage.getItem('miaobi-document:' + document.querySelector('[aria-label="选择项目"]')?.value));
  if (!articleId) throw new Error('没有选中独立指标验收文章');
  const detailUrl = `${base}/api/v1/writing/documents/${articleId}`;
  const before = await (await page.request.get(detailUrl)).json();
  const beforeVersion = before.current_version.id;
  const openPreview = async () => {
    await page.getByRole('button', { name: '修改输入指标 · 预览正文影响' }).click();
    const input = page.getByRole('dialog', { name: '修改输入指标' });
    await expect(input).toBeVisible();
    await input.getByLabel('原始指标').selectOption('rescue_available');
    await input.getByLabel('修改后的值').fill('400');
    await input.getByRole('button', { name: '查看影响' }).click();
    const impact = page.getByRole('dialog', { name: '指标变更影响预览' });
    await expect(impact).toBeVisible({ timeout: 30000 });
    await expect(impact).toContainText('180 → 100');
    await expect(impact).toContainText('可用搜救人员400人，缺口100人');
    await expect(impact.locator('.impact-proposal')).toHaveCount(4);
    return impact;
  };
  const first = await openPreview();
  const unchanged = await (await page.request.get(detailUrl)).json();
  expect(unchanged.current_version.id).toBe(beforeVersion);
  await first.getByRole('button', { name: '取消', exact: true }).click();
  await expect(first).not.toBeVisible();
  const second = await openPreview();
  const summary = second.locator('.impact-proposal').filter({ hasText: '当前缺口180人' });
  await summary.locator('input[type=checkbox]').uncheck();
  await expect(second.getByRole('button', { name: '应用选中的 3 处' })).toBeEnabled();
  await second.getByRole('button', { name: '应用选中的 3 处' }).click();
  await expect(second).not.toBeVisible({ timeout: 30000 });
  const after = await (await page.request.get(detailUrl)).json();
  expect(after.current_version.id).not.toBe(beforeVersion);
  const nodes = new Map(after.current_version.content.map(n => [n.id, n]));
  expect(nodes.get('metric-rescue-gap').value).toBe(100);
  expect(JSON.stringify(nodes.get('p-resource'))).toContain('可用搜救人员400人，缺口100人');
  expect(JSON.stringify(nodes.get('table-rescue'))).toContain('可用搜救人员400人');
  expect(JSON.stringify(nodes.get('table-rescue'))).toContain('缺口100人');
  expect(JSON.stringify(nodes.get('p-summary'))).toContain('当前缺口180人');
  expect(nodes.get('p-summary').freshness_status).toBe('stale');
  expect(JSON.stringify(nodes.get('p-unrelated'))).toContain('其他业务安排保持不变');
  await expect(page.locator('[data-slate-editor]')).toContainText('搜救人员缺口为100人', { timeout: 30000 });
  for (const [width, height] of [[1280, 720], [1440, 900]]) {
    await page.setViewportSize({ width, height });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  }
  if (errors.length) throw new Error('Browser console: ' + errors.join(' | '));
  console.log(JSON.stringify({ passed: true, project: projectName, article_id: articleId,
    before_version: beforeVersion, after_version: after.current_version.id,
    checks: ['真实登录', '完整 Plate 编辑器', '指标选择', '变更前弹窗', '取消不修改正文',
      '逐项勾选接受', '测算块+正文+表格同步', '未勾选段落保留并标记待核对',
      '无关正文未变化', '历史版本保留', '1280×720', '1440×900', 'Console 无错误'] }, null, 2));
} finally {
  await browser.close();
}
