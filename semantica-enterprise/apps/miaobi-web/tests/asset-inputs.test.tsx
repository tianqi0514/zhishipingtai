import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Facts } from '../src/App';
import type { Fact, Project } from '../src/types/domain';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const project = { id: 'project', input_contract: { properties: {} } } as Project;
const empty = { id: 'input', fact_key: 'required', label: '本次需求人数', unit: '人', value: {},
  source_locator: { value_type: 'number' }, source_type: 'manual_input', fact_type: 'manual_input',
  verification_status: 'unverified', freshness_status: 'current', version: 1 } as Fact;

it('定义采用后空输入只能填写；取消不提交，填写0保存数字而不是字符串', async () => {
  const requests = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response('{}', { headers: { 'content-type': 'application/json' } }));
  vi.stubGlobal('fetch', requests);
  render(<Facts project={project} facts={[empty]} onChanged={vi.fn()} onError={vi.fn()} />);
  expect(screen.queryByRole('button', { name: '确认' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '修正' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '填写' }));
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(requests).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '填写' }));
  fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: '保存待确认' }));
  await waitFor(() => expect(requests).toHaveBeenCalledTimes(1));
  const options = requests.mock.calls[0][1] as RequestInit;
  expect(JSON.parse(String(options.body))).toMatchObject({ value: { number: 0 }, verification_status: 'unverified', source_locator: { value_type: 'number' } });
});
