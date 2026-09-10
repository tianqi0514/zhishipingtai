/** Real browser acceptance. Credentials stay in memory; no mocked requests. */
import { createRequire } from 'node:module';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
const root = resolve(import.meta.dirname, '../..');
const require = createRequire(resolve(root, 'apps/miaobi-web/package.json'));
const { chromium, expect } = require('@playwright/test');
const output = resolve(root, '.demo-build/semantic-writing-browser');
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: chromium.executablePath() });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const page = await context.newPage();
const errors = [], checks = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
try {
  await page.goto(process.env.MIAOBI_DEMO_URL || 'http://localhost:8080/');
  const password = process.env.MIAOBI_DEMO_PASSWORD || execFileSync('docker', ['compose', 'exec', '-T', 'api', 'python', '-c', 'import os; print(os.environ["BOOTSTRAP_ADMIN_PASSWORD"])'], { cwd: root, encoding: 'utf8' }).trim();
  await page.locator('[name=username]').fill(process.env.MIAOBI_DEMO_USERNAME || 'admin');
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('#logout')).toBeVisible({ timeout: 30000 });
  checks.push('真实登录');
  errors.length = 0; // Anonymous /auth/me = 401 is the login probe, not an application failure.
  await page.goto((process.env.MIAOBI_DEMO_URL || 'http://localhost:8080') + '/miaobi/');
  const select = page.getByLabel('选择方案任务');
  await expect(select).toBeVisible({ timeout: 45000 });
  const projects = await select.locator('option').allTextContents();
  const name = process.env.MIAOBI_TEST_PROJECT_NAME || projects.find(n => n.startsWith('安置点供水保障报告'));
  if (!name) throw new Error('先运行真实 API 验收创建方案任务');
  await select.selectOption({ label: name });
  const selectedProjectId = await select.inputValue();
  await expect(page.locator('.workflow-strip')).toBeVisible({ timeout: 30000 });
  checks.push('选择实际验收任务');
  if (process.env.MIAOBI_BROWSER_INSPECT === '1') {
    console.log(await page.locator('body').innerText());
    await page.screenshot({ path: resolve(output, 'task.png'), fullPage: true });
  } else {
    await page.locator('.workflow-strip').getByRole('button', { name: /分析计算/ }).click();
    await expect(page.getByRole('heading', { name: '章节依据', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '重新检查', exact: true }).click();
    await expect(page.getByText('安置点A — 受到影响 → 供水中断', { exact: false }).first()).toBeVisible({ timeout: 30000 });
    await page.getByRole('button', { name: '确认用于写作', exact: true }).click();
    checks.push('真实准备、查看规则结论、确认章节依据');
    await page.locator('.sidebar').getByRole('button', { name: '报告编辑', exact: true }).click();
    await expect(page.locator('[data-slate-editor]')).toBeVisible({ timeout: 45000 });
    await page.getByRole('button', { name: '来源与计算', exact: true }).click();
    await expect(page.getByRole('region', { name: '正文段落依据' })).toBeVisible();
    checks.push('完整 Plate 正文及右侧段落依据');
    const proof = page.getByRole('region', { name: '正文段落依据' }).locator('details').filter({ hasText: '安置点A — 受到影响 → 供水中断' }).first();
    await proof.locator('summary').first().click();
    await proof.getByRole('button', { name: /01-安置点设施清单/ }).first().click();
    await expect(page.locator('.source-preview')).toContainText('供水站C');
    await page.getByRole('button', { name: '关闭来源', exact: true }).click();
    checks.push('真实规则前提、上传原文来源可打开');
    if (process.env.MIAOBI_BROWSER_AGENT_EDIT === '1') {
      const passage = page.locator('[data-slate-string]').filter({ hasText: '针对供水中断影响' }).first();
      await passage.scrollIntoViewIfNeeded();
      await passage.evaluate(element => {
        const range = document.createRange(); range.selectNodeContents(element);
        const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
        document.dispatchEvent(new Event('selectionchange'));
      });
      await passage.click({ button: 'right' });
      const menu = page.getByRole('dialog', { name: '妙笔智能修改' });
      await expect(menu).toBeVisible();
      await menu.getByRole('button', { name: '缩写', exact: true }).click();
      await expect.poll(async () => {
        const failure = menu.locator('.agent-edit-error');
        if (await failure.isVisible()) return `failed: ${await failure.innerText()}`;
        return await menu.getByRole('button', { name: '应用建议' }).isVisible() ? 'ready' : 'running';
      }, { timeout: 180000 }).toBe('ready');
      const proposal = await menu.locator('.agent-edit-preview p').last().innerText();
      expect(proposal).toContain('现场');
      expect(proposal).not.toContain('不得凭空生成');
      await menu.getByRole('button', { name: '应用建议' }).click();
      await page.locator('.editor-toolbar').getByRole('button', { name: '保存', exact: true }).click();
      await expect(page.locator('[data-slate-editor]')).toContainText(proposal);
      checks.push('真实右键缩写、DSH建议、人工点击应用并保存');
    }
    for (const [width, height] of [[1280, 720], [1440, 900], [1920, 1080]]) {
      await page.setViewportSize({ width, height });
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      await expect(page.locator('.editor-toolbar')).toBeVisible();
      await page.screenshot({ path: resolve(output, `editor-${width}.png`), fullPage: true });
      checks.push(`${width}×${height} 无整体横向溢出`);
    }
    await page.getByRole('button', { name: '审校发布', exact: true }).click();
    await page.getByRole('button', { name: /执行生产质量检查|重新执行质量检查/ }).click();
    await expect(page.getByText('审校通过，可以生成正式文件。')).toBeVisible({ timeout: 30000 });
    for (const format of ['DOCX', 'PDF', '依据报告']) {
      const outputFormat = format === '依据报告' ? 'evidence_docx' : format.toLowerCase();
      const filename = format === '依据报告' ? '安置点供水保障报告-生成依据.docx' : `安置点供水保障报告.${outputFormat}`;
      const exportResponse = page.waitForResponse(r => r.url().endsWith('/exports') && r.request().method() === 'POST' && r.request().postDataJSON()?.output_format === outputFormat);
      await page.getByRole('button', { name: format, exact: true }).click();
      const exported = await (await exportResponse).json();
      expect(exported.status).toBe('succeeded');
      const link = page.locator(`.sidebar-export-history a[href="/api/v1/writing/exports/${exported.id}/download"]`);
      await expect(link).toContainText('下载', { timeout: 60000 });
      const waiting = page.waitForEvent('download');
      await link.click();
      const download = await waiting;
      await download.saveAs(resolve(output, filename));
      expect(readFileSync(resolve(output, filename)).subarray(0, format === 'PDF' ? 4 : 2).toString()).toBe(format === 'PDF' ? '%PDF' : 'PK');
      checks.push(`${format} 真实生成并点击下载`);
      if (format === '依据报告') {
        const base = (process.env.MIAOBI_DEMO_URL || 'http://localhost:8080').replace(/\/$/, '');
        const documentResponse = await page.request.get(`${base}/api/v1/writing/documents/${exported.document_id}`);
        const bindingResponse = await page.request.get(`${base}/api/v1/writing/documents/${exported.document_id}/bindings`);
        expect(documentResponse.ok()).toBe(true); expect(bindingResponse.ok()).toBe(true);
        const document = await documentResponse.json();
        expect(document.current_version.id).toBe(exported.document_version_id);
        const visible = new Set();
        const visit = nodes => { for (const node of nodes) { if (node.id) visible.add(node.id); visit(node.children || []); } };
        visit(document.current_version.content);
        const allBindings = await bindingResponse.json();
        const expected = new Map(allBindings.filter(b => visible.has(b.block_id) && b.block_type === 'knowledge_citation')
          .map(b => [Number(b.metadata_json.citation_number), b]));
        expect(expected.size).toBeGreaterThan(0);
        const xml = execFileSync('unzip', ['-p', resolve(output, filename), 'word/document.xml'], { encoding: 'utf8' });
        const text = xml.replace(/<\/w:p>/g, '\n').replace(/<[^>]+>/g, '')
          .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, '&');
        const actual = [...text.matchAll(/^\[(\d+)\]/gm)].map(m => Number(m[1]));
        expect([...new Set(actual)].sort((a, b) => a - b)).toEqual([...expected.keys()].sort((a, b) => a - b));
        for (const [number, binding] of expected) {
          const metadata = binding.metadata_json;
          if (metadata.knowledge_evidence?.length) {
            for (const reference of metadata.knowledge_evidence) {
              expect(text).toContain(`[${number}] ${reference.kind === 'inference' ? '规则结论' : '已有关系'}：${reference.text}`);
              for (const premise of reference.premises || []) expect(text).toContain(`来源：${premise.source.title}`);
            }
          } else expect(text).toContain(`[${number}] ${metadata.source_title}`);
        }
        checks.push('依据报告引用编号、当前版本来源和全部规则前提逐项一致');
      }
    }
    await page.reload();
    await expect(select).toHaveValue(selectedProjectId, { timeout: 30000 });
    await page.locator('.sidebar').getByRole('button', { name: '报告编辑', exact: true }).click();
    await expect(page.locator('[data-slate-editor]')).toContainText('供水中断', { timeout: 30000 });
    checks.push('刷新恢复任务');
    expect(errors).toEqual([]);
  }
  writeFileSync(resolve(output, process.env.MIAOBI_BROWSER_AGENT_EDIT === '1' ? 'result-agent-edit.json' : 'result.json'), JSON.stringify({ checks, errors }, null, 2));
  console.log(JSON.stringify({ checks, errors }));
} finally { await browser.close(); }
