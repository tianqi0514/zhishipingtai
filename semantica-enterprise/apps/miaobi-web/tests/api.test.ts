import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../src/api';

afterEach(() => vi.unstubAllGlobals());

describe('API 错误文案', () => {
  it('提取质量检查的首条有效问题，不展示原始 JSON 或敏感诊断字段', async () => {
    const detail = {
      ok: false,
      issues: [
        { code: 'invalid_agent_report', severity: 'error', message: 'Agent 未返回唯一且完整的分章节 JSON。' },
        { message: '第二条问题' },
      ],
      raw_output: '{"private_data":"内部正文"}',
      secret: 'do-not-display-this-secret',
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), {
      status: 422, headers: { 'content-type': 'application/json' },
    })));

    const error = await api('/writing/generation-runs/run-1/finalize', { method: 'POST' }).catch((reason) => reason);
    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw new Error('Expected an ApiError');
    expect(error.status).toBe(422);
    expect(error.detail).toEqual(detail);
    expect(error.message).toBe('Agent 未返回唯一且完整的分章节 JSON。');
    expect(error.message).not.toContain('第二条问题');
    expect(error.message).not.toContain(detail.secret);
    expect(error.message).not.toContain(detail.raw_output);
  });

  it('兼容已有字符串错误，不修改状态码或原始 detail', () => {
    const error = new ApiError(409, '生成期间正文已被修改，请重新生成；您的修改已保留');
    expect(error.message).toBe('生成期间正文已被修改，请重新生成；您的修改已保留');
    expect(error.status).toBe(409);
    expect(error.detail).toBe(error.message);
  });

  it('跳过缺失或非字符串的问题消息', () => {
    const error = new ApiError(422, { issues: [null, '原始输出', { message: {} }, { message: '   ' }, { message: '  请补齐章节。  ' }] });
    expect(error.message).toBe('请补齐章节。');
  });

  it.each([null, undefined, {}, [], { issues: 'secret' }, { issues: [{ message: { secret: 'hidden' } }] }])(
    '未知错误结构继续使用通用消息（%j）', (detail) => {
      expect(new ApiError(500, detail).message).toBe('请求处理失败');
    },
  );
});
