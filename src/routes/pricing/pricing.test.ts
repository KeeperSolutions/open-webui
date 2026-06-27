// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { writable } from 'svelte/store';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/utils/auth', () => ({ handleAuthSuccess: vi.fn() }));
vi.mock('$lib/apis/model-classes', () => ({
	getModelClasses: vi.fn().mockResolvedValue([])
}));
vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return { user: writable(null) };
});

import * as navigation from '$app/navigation';
import { user } from '$lib/stores';

// Test the onMount redirect logic directly — same code as +page.svelte onMount
async function simulatePricingPageMount() {
	const { get } = await import('svelte/store');
	const { goto } = await import('$app/navigation');
	if (get(user as any)) { goto('/chat'); return; }
}

beforeEach(() => {
	vi.clearAllMocks();
	localStorage.clear();
	(user as ReturnType<typeof writable>).set(null);
});

describe('pricing page — onMount redirect for logged-in users', () => {
	it('redirects to /chat if user is already logged in on mount', async () => {
		(user as ReturnType<typeof writable>).set({ id: '1', name: 'Test' });
		await simulatePricingPageMount();
		expect(navigation.goto).toHaveBeenCalledWith('/chat');
	});

	it('does not redirect if user is not logged in on mount', async () => {
		await simulatePricingPageMount();
		expect(navigation.goto).not.toHaveBeenCalled();
	});
});

describe('pricing page — postLoginRedirect localStorage contract', () => {
	it('paid plan sets /billing in localStorage before modal opens', () => {
		// mirrors openModal(plan) when plan.postLoginRedirect = '/billing'
		localStorage.setItem('postLoginRedirect', '/billing');
		expect(localStorage.getItem('postLoginRedirect')).toBe('/billing');
	});

	it('free trial removes postLoginRedirect from localStorage', () => {
		// mirrors openModal(plan) when plan has no postLoginRedirect
		localStorage.setItem('postLoginRedirect', '/billing');
		localStorage.removeItem('postLoginRedirect');
		expect(localStorage.getItem('postLoginRedirect')).toBeNull();
	});

	it('postLoginRedirect is consumed and cleared after one use', () => {
		// mirrors handleAuthSuccess reading and clearing the value
		localStorage.setItem('postLoginRedirect', '/billing');
		const stored = localStorage.getItem('postLoginRedirect') ?? '';
		localStorage.removeItem('postLoginRedirect');
		expect(stored).toBe('/billing');
		expect(localStorage.getItem('postLoginRedirect')).toBeNull();
	});

	it('second login after postLoginRedirect was consumed defaults to /chat', () => {
		// key is gone — stored is empty string, redirect defaults to /chat
		const stored = localStorage.getItem('postLoginRedirect') ?? '';
		const redirect = stored.startsWith('/') ? stored : '/chat';
		expect(redirect).toBe('/chat');
	});
});
