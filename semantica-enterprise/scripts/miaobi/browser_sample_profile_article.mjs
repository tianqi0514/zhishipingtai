/** Live browser acceptance for the isolated PDF-profile writing project. */
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { mkdtempSync } from 'node:fs';

const root = resolve(import.meta.dirname, '../..');
const require = createRequire(resolve(root, 'apps/miaobi-web/package.json'));
const { chromium, expect } = require('@playwright/test');
const base = (process.env.MIAOBI_DEMO_URL || 'http://localhost:8080').replace(/\/$/, '');
const projectName = '临夏州地震应急预案讨论稿·样稿结构验证';
const articleTitle = '临夏州地震应急预案（项目讨论稿）';
const expectedChapters = ['总则', '组织体系', '运行机制', '应急保障', '其他地震事件应急', '监督管理', '附则'];
const browser = await chromium.launch({ headless: true, executablePath: chromium.executablePath() });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });

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
  const project = page.getByLabel('选择项目');
  await expect(project).toBeVisible({ timeout: 45000 });
  await project.selectOption({ label: projectName });
  await page.locator('.sidebar').getByRole('button', { name: '项目', exact: true }).click();
  await expect(page.getByText('提取目录和写作要求')).toBeVisible({ timeout: 45000 });
  const sampleRole = page.locator('select[aria-label^="设置样稿.pdf的材料角色"]');
  await expect(sampleRole).toHaveValue('sample_style');
  const factualRole = page.locator('select[aria-label^="设置临夏州地震应急预案.docx的材料角色"]');
  await expect(factualRole).toHaveValue('policy_basis');
  await page.getByRole('button', { name: '查看已确认配置' }).click();
  await expect(page.locator('.sample-chapter-row')).toHaveCount(7);
  for (const [index, chapter] of expectedChapters.entries()) {
    await expect(page.locator('.sample-chapter-row').nth(index)).toContainText(chapter);
  }

  await page.locator('.sidebar').getByRole('button', { name: '报告编辑', exact: true }).click();
  const article = page.locator('.document-switcher select');
  await expect(article).toBeVisible({ timeout: 45000 });
  await article.selectOption({ label: articleTitle });
  const editor = page.locator('[data-slate-editor]');
  await expect(editor).toBeVisible({ timeout: 45000 });
  for (const chapter of expectedChapters) await expect(editor).toContainText(chapter);
  const chapterHeadings = editor.locator('h2');
  await expect(chapterHeadings).toHaveCount(7);
  const citation = editor.getByRole('button', { name: '查看知识引用依据' }).first();
  await expect(citation).toBeVisible();
  const [fragmentResponse] = await Promise.all([
    page.waitForResponse(response => response.url().includes('/knowledge/fragments/') && response.request().method() === 'GET', { timeout: 30000 }),
    citation.click(),
  ]);
  expect(fragmentResponse.status()).toBe(200);
  const fragment = await fragmentResponse.json();
  expect(fragment.document_title).toContain('临夏州地震应急预案');
  expect(String(fragment.text || '').length).toBeGreaterThan(20);
  await expect(page.locator('.binding-inspector')).toBeVisible({ timeout: 30000 });
  await expect(page.locator('.binding-inspector')).toContainText('临夏州地震应急预案', { timeout: 30000 });
  for (const [width, height] of [[1280, 720], [1440, 900]]) {
    await page.setViewportSize({ width, height });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  }
  let exportPath = null;
  if (process.env.MIAOBI_RENDER_DOCX === '1') {
    await page.getByRole('button', { name: '审校发布', exact: true }).click();
    await page.getByRole('button', { name: '执行生产质量检查' }).click();
    await expect(page.getByText('审校通过，可以生成正式文件。')).toBeVisible({ timeout: 30000 });
    const [exportResponse] = await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/exports') && response.request().method() === 'POST', { timeout: 120000 }),
      page.getByRole('button', { name: 'DOCX', exact: true }).click(),
    ]);
    expect(exportResponse.status()).toBe(200);
    const job = await exportResponse.json();
    expect(job.status).toBe('succeeded');
    const link = page.locator(`a[href="/api/v1/writing/exports/${job.id}/download"]`);
    await expect(link).toBeVisible({ timeout: 30000 });
    const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
    const exportDir = mkdtempSync(resolve(root, '.local-dev/miaobi-sample-export-'));
    exportPath = resolve(exportDir, '临夏州地震应急预案项目讨论稿.docx');
    await download.saveAs(exportPath);
  }
  if (errors.length) throw new Error('Browser console: ' + errors.join(' | '));
  console.log(JSON.stringify({ passed: true, project: projectName, article: articleTitle, export_path: exportPath,
    checks: ['真实登录', '样稿与业务依据角色分离', '已确认配置刷新后恢复', '七章可编辑结构', 'Plate 正式文章',
      '七个一级标题', '引用点击打开真实片段接口', '1280×720', '1440×900', 'Console 无错误'] }, null, 2));
} finally {
  await browser.close();
}
