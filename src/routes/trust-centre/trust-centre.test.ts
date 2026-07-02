// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { writable } from 'svelte/store';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/utils/auth', () => ({ handleAuthSuccess: vi.fn() }));
vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return { user: writable(null) };
});

import * as navigation from '$app/navigation';
import { user } from '$lib/stores';

async function simulateMount() {
	const { get } = await import('svelte/store');
	const { goto } = await import('$app/navigation');
	if (get(user as any)) { goto('/chat'); return; }
}

beforeEach(() => {
	vi.clearAllMocks();
	(user as ReturnType<typeof writable>).set(null);
});

describe('trust-centre page — onMount redirect', () => {
	it('redirects to /chat when a user is logged in', async () => {
		(user as ReturnType<typeof writable>).set({ id: '1', name: 'Test' });
		await simulateMount();
		expect(navigation.goto).toHaveBeenCalledWith('/chat');
	});

	it('does not redirect when no user is present', async () => {
		await simulateMount();
		expect(navigation.goto).not.toHaveBeenCalled();
	});
});
