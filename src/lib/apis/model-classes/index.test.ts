// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock SvelteKit app modules used by $lib/constants
vi.mock('$app/environment', () => ({ browser: false, dev: false, building: false }));

import * as api from './index';

describe('model-classes frontend API wrappers', () => {
	const ORIGINAL_FETCH = globalThis.fetch;

	beforeEach(() => {
		vi.restoreAllMocks();
	});

	afterEach(() => {
		// restore fetch if we replaced it
		// @ts-expect-error - test cleanup
		globalThis.fetch = ORIGINAL_FETCH;
	});

	function mockFetchOnce(response: Partial<Response> & { json?: () => Promise<any> }) {
		const res = {
			ok: true,
			status: 200,
			json: async () => ({}),
			...response
		} as Response;
		// @ts-expect-error - test stub
		globalThis.fetch = vi.fn().mockResolvedValue(res);
		return globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
	}

	function mockFetchErrorOnce(payload: any) {
		const res = {
			ok: false,
			status: 400,
			json: async () => payload
		} as unknown as Response;
		// @ts-expect-error
		globalThis.fetch = vi.fn().mockResolvedValue(res);
		return globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
	}

	it('getModelClasses passes token and returns parsed list', async () => {
		const fetchMock = mockFetchOnce({
			ok: true,
			json: async () => [{ id: 1, name: 'x', order: 1, credit_burn: 1, created_at: 0, updated_at: 0 }]
		});
		const out = await api.getModelClasses('tok123');
		expect(fetchMock).toHaveBeenCalledTimes(1);
		const [url, init] = (fetchMock as any).mock.calls[0];
		expect(String(url)).toMatch(/\/model-classes\/$/);
		expect((init?.headers as any)?.authorization).toBe('Bearer tok123');
		expect(out?.length).toBe(1);
	});

	it('getModelClasses throws on non-2xx', async () => {
		mockFetchErrorOnce({ error: 'nope' });
		await expect(api.getModelClasses('t')).rejects.toMatchObject({ error: 'nope' });
	});

	it('createModelClass sends POST with body and token, returns created', async () => {
		const created = { id: 2, name: 'new', credit_burn: 2.5, order: 2, created_at: 1, updated_at: 1 };
		const fetchMock = mockFetchOnce({ ok: true, json: async () => created });
		const res = await api.createModelClass('tok', { name: 'new', credit_burn: 2.5 });
		const [url, init] = (fetchMock as any).mock.calls[0];
		expect(String(url)).toMatch(/\/model-classes\/$/);
		expect(init?.method).toBe('POST');
		expect((init?.headers as any)?.authorization).toBe('Bearer tok');
		expect(JSON.parse(init?.body as string)).toMatchObject({ name: 'new', credit_burn: 2.5 });
		expect(res).toEqual(created);
	});

	it('createModelClass throws on error', async () => {
		mockFetchErrorOnce({ detail: 'bad' });
		await expect(api.createModelClass('t', { name: 'x', credit_burn: 1 })).rejects.toMatchObject({ detail: 'bad' });
	});

	it('updateModelClass sends PUT with id and body', async () => {
		const updated = { id: 3, name: 'u', credit_burn: 9, order: 3, created_at: 0, updated_at: 9 };
		const fetchMock = mockFetchOnce({ ok: true, json: async () => updated });
		const res = await api.updateModelClass('tok', 3, { name: 'u', credit_burn: 9 });
		const [url, init] = (fetchMock as any).mock.calls[0];
		expect(String(url)).toMatch(/\/model-classes\/3$/);
		expect(init?.method).toBe('PUT');
		expect(JSON.parse(init?.body as string)).toMatchObject({ name: 'u' });
		expect(res).toEqual(updated);
	});

	it('updateModelClass throws on error', async () => {
		mockFetchErrorOnce({ detail: 'nf' });
		await expect(api.updateModelClass('t', 1, { name: 'x', credit_burn: 1 })).rejects.toMatchObject({ detail: 'nf' });
	});

	it('deleteModelClass sends DELETE and returns message', async () => {
		const fetchMock = mockFetchOnce({ ok: true, json: async () => ({ message: 'Model class deleted' }) });
		const res = await api.deleteModelClass('tok', 7);
		const [url, init] = (fetchMock as any).mock.calls[0];
		expect(String(url)).toMatch(/\/model-classes\/7$/);
		expect(init?.method).toBe('DELETE');
		expect(res).toEqual({ message: 'Model class deleted' });
	});

	it('deleteModelClass throws on error', async () => {
		mockFetchErrorOnce({ detail: 'not found' });
		await expect(api.deleteModelClass('t', 1)).rejects.toMatchObject({ detail: 'not found' });
	});

	it('reorderModelClasses sends POST /reorder with items', async () => {
		const list = [{ id: 1, name: 'a', order: 2, credit_burn: 1, created_at: 0, updated_at: 0 }];
		const fetchMock = mockFetchOnce({ ok: true, json: async () => list });
		const res = await api.reorderModelClasses('tok', [{ id: 1, order: 2 }]);
		const [url, init] = (fetchMock as any).mock.calls[0];
		expect(String(url)).toMatch(/\/model-classes\/reorder$/);
		expect(init?.method).toBe('POST');
		expect(JSON.parse(init?.body as string)).toEqual([{ id: 1, order: 2 }]);
		expect(res).toEqual(list);
	});

	it('reorderModelClasses throws on error', async () => {
		mockFetchErrorOnce({ detail: 'dup' });
		await expect(api.reorderModelClasses('t', [{ id: 1, order: 1 }])).rejects.toMatchObject({ detail: 'dup' });
	});

	// Exercise the exported TS interfaces at runtime to ensure shapes are usable and stay in sync with usage.
	it('interfaces are exported and usable (type + minimal runtime shape check)', () => {
		const mc: api.ModelClass = {
			id: 1,
			name: 'x',
			models: ['a'],
			credit_burn: 1,
			created_at: 0,
			updated_at: 0,
			order: 1
		};
		const form: api.ModelClassForm = { name: 'y', credit_burn: 2 };
		const ri: api.ReorderItem = { id: 1, order: 10 };
		expect(mc.id).toBe(1);
		expect(form.name).toBe('y');
		expect(ri.order).toBe(10);
	});
});
