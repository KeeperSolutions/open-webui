// @vitest-environment jsdom
/**
 * The masking default toggle in the dashboard header. Turning it off asks first,
 * because every user who never chose loses masking.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/svelte';
import { get, readable } from 'svelte/store';

vi.mock('svelte-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('$lib/apis/users', () => ({ setPiiMaskingDefault: vi.fn() }));
// jsdom has no layout, so focus-trap finds no tabbable node in ConfirmDialog.
vi.mock('focus-trap', () => ({
	createFocusTrap: () => ({ activate: () => {}, deactivate: () => {} })
}));

import { setPiiMaskingDefault } from '$lib/apis/users';
import { config } from '$lib/stores';
import { toast } from 'svelte-sonner';

import DefaultMaskingToggle from './DefaultMaskingToggle.svelte';
import type { AccessUser } from './sections/usersAccess';

const i18n = readable({
	t: (k: string, vars?: Record<string, unknown>) =>
		vars ? k.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars[name] ?? '')) : k
});

const RELOAD_NOTE = 'Users with the app already open get the change after they reload.';

const account = (over: Partial<AccessUser> = {}): AccessUser => ({
	id: 'u1',
	name: 'Ana',
	email: 'ana@x.com',
	role: 'user',
	pii_masking_enforced: false,
	pii_policy_group_ids: [],
	...over
});

const mount = (props: Record<string, unknown> = {}) =>
	render(DefaultMaskingToggle, {
		props: { users: [account()], ...props },
		context: new Map([['i18n', i18n]])
	});

const toggle = () =>
	document.querySelector<HTMLButtonElement>(
		'[data-testid="pii-default-control"] button[aria-pressed]'
	)!;
const dialogButton = (label: string) =>
	[...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === label);
const flush = () => new Promise((r) => setTimeout(r, 0));

describe('the masking default toggle', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		config.set({ features: { pii_masking_default: true } } as never);
	});

	it('shows the current default', () => {
		mount();
		expect(toggle().getAttribute('aria-pressed')).toBe('true');
		config.set({ features: { pii_masking_default: false } } as never);
		return flush().then(() => expect(toggle().getAttribute('aria-pressed')).toBe('false'));
	});

	it('asks before turning off, then saves and updates the config', async () => {
		vi.mocked(setPiiMaskingDefault).mockResolvedValue({ enabled: false } as never);
		const onChanged = vi.fn();
		mount({
			users: [account(), account({ id: 'u2', pii_masking_enforced: true })],
			onChanged
		});
		toggle().click();
		await flush();

		expect(setPiiMaskingDefault).not.toHaveBeenCalled();
		// The enforced user does not follow the default, so the dialog counts one.
		expect(document.body.textContent).toContain('1 user who has not chosen');
		expect(document.body.textContent).toContain(RELOAD_NOTE);

		dialogButton('Confirm')!.click();
		await flush();

		expect(setPiiMaskingDefault).toHaveBeenCalledWith(localStorage.token, false);
		expect(toast.success).toHaveBeenCalledWith(`Default PII masking updated. ${RELOAD_NOTE}`);
		expect(get(config)?.features?.pii_masking_default).toBe(false);
		expect(toggle().getAttribute('aria-pressed')).toBe('false');
		expect(onChanged).toHaveBeenCalled();
	});

	it('turns back on without asking', async () => {
		vi.mocked(setPiiMaskingDefault).mockResolvedValue({ enabled: true } as never);
		config.set({ features: { pii_masking_default: false } } as never);
		mount();
		toggle().click();
		await flush();

		expect(setPiiMaskingDefault).toHaveBeenCalledWith(localStorage.token, true);
		expect(get(config)?.features?.pii_masking_default).toBe(true);
	});

	it('counts the listed users as a lower bound when the directory is truncated', async () => {
		mount({ users: [account(), account({ id: 'u2' })], truncated: true });
		toggle().click();
		await flush();

		expect(document.body.textContent).toContain('At least 2 users who have not chosen');
	});

	it('changes nothing when the confirmation is cancelled', async () => {
		mount();
		toggle().click();
		await flush();

		dialogButton('Cancel')!.click();
		await flush();

		expect(setPiiMaskingDefault).not.toHaveBeenCalled();
		expect(get(config)?.features?.pii_masking_default).toBe(true);
		expect(toggle().getAttribute('aria-pressed')).toBe('true');
	});

	it('keeps the default and reports the error when saving fails', async () => {
		vi.mocked(setPiiMaskingDefault).mockRejectedValue('Not allowed');
		mount();
		toggle().click();
		await flush();
		dialogButton('Confirm')!.click();
		await flush();

		expect(toast.error).toHaveBeenCalledWith('Not allowed');
		expect(get(config)?.features?.pii_masking_default).toBe(true);
		expect(toggle().getAttribute('aria-pressed')).toBe('true');
	});
});
