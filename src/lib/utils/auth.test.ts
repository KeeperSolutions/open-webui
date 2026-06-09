// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('svelte-sonner', () => ({ toast: { success: vi.fn() } }));
vi.mock('$lib/apis', () => ({ getBackendConfig: vi.fn().mockResolvedValue({}) }));
vi.mock('$lib/apis/auths', () => ({ updateUserTimezone: vi.fn() }));
vi.mock('$lib/utils', () => ({ getUserTimezone: vi.fn().mockReturnValue(null) }));
vi.mock('$lib/utils/theme', () => ({ applyThemeFromLocalStorage: vi.fn() }));
vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		config: writable({}),
		user: writable(null),
		socket: writable(null)
	};
});

import * as navigation from '$app/navigation';
import { handleAuthSuccess } from './auth';

const sessionUser = { id: '1', name: 'Test', email: 'test@example.com', token: 'tok' } as any;

beforeEach(() => {
	vi.clearAllMocks();
	localStorage.clear();
	Object.defineProperty(window, 'matchMedia', {
		writable: true,
		value: vi.fn().mockReturnValue({ matches: false })
	});
});

describe('handleAuthSuccess — post-login redirect', () => {
	it('goes to /chat when no postLoginRedirect is set', async () => {
		await handleAuthSuccess(sessionUser);
		expect(navigation.goto).toHaveBeenCalledWith('/chat');
	});

	it('goes to /billing when postLoginRedirect is /billing', async () => {
		localStorage.setItem('postLoginRedirect', '/billing');
		await handleAuthSuccess(sessionUser);
		expect(navigation.goto).toHaveBeenCalledWith('/billing');
	});

	it('goes to /chat when postLoginRedirect is explicitly /chat', async () => {
		localStorage.setItem('postLoginRedirect', '/chat');
		await handleAuthSuccess(sessionUser);
		expect(navigation.goto).toHaveBeenCalledWith('/chat');
	});

	it('clears postLoginRedirect from localStorage after use', async () => {
		localStorage.setItem('postLoginRedirect', '/billing');
		await handleAuthSuccess(sessionUser);
		expect(localStorage.getItem('postLoginRedirect')).toBeNull();
	});

	it('falls back to /chat for a value not starting with /', async () => {
		localStorage.setItem('postLoginRedirect', 'https://evil.com');
		await handleAuthSuccess(sessionUser);
		expect(navigation.goto).toHaveBeenCalledWith('/chat');
	});

	it('falls back to /chat for an empty string value', async () => {
		localStorage.setItem('postLoginRedirect', '');
		await handleAuthSuccess(sessionUser);
		expect(navigation.goto).toHaveBeenCalledWith('/chat');
	});

	it('second call uses its own redirect — key is cleared after first call', async () => {
		localStorage.setItem('postLoginRedirect', '/billing');
		await handleAuthSuccess(sessionUser);
		await handleAuthSuccess(sessionUser);
		expect(navigation.goto).toHaveBeenNthCalledWith(1, '/billing');
		expect(navigation.goto).toHaveBeenNthCalledWith(2, '/chat');
	});
});
