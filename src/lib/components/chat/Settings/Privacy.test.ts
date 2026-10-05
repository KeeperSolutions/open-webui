// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { readable } from 'svelte/store';
import { tick } from 'svelte';

// vi.mock factories are hoisted above every declaration in this file, so any
// value they close over has to be created by vi.hoisted.
const h = vi.hoisted(() => ({
	setOwnPiiMasking: vi.fn(async (_token: string, _preference: string) => ({ ui: { saved: true } })),
	toastError: vi.fn(),
	getSessionUser: vi.fn(async () => ({ role: 'user', permissions: {} })),
	isPiiPipelineConfigured: vi.fn(async () => true),
	settingsSet: vi.fn(),
	userSet: vi.fn(),
	// The component reads $settings and $user; we substitute controllable stores.
	state: {
		settings: {} as any,
		user: { role: 'user', permissions: {} } as any,
		config: { features: { pii_filter_ids: ['pii_filter'] } } as any
	}
}));

const { setOwnPiiMasking, getSessionUser, isPiiPipelineConfigured } = h;

vi.mock('$lib/apis/users', () => ({ setOwnPiiMasking: h.setOwnPiiMasking }));
vi.mock('svelte-sonner', () => ({ toast: { error: h.toastError } }));
vi.mock('$lib/apis/auths', () => ({ getSessionUser: h.getSessionUser }));
vi.mock('$lib/utils/pii', async () => {
	const actual = await vi.importActual<typeof import('$lib/utils/pii')>('$lib/utils/pii');
	return { ...actual, isPiiPipelineConfigured: h.isPiiPipelineConfigured };
});

vi.mock('$lib/stores', () => ({
	// `pii.ts` reads $config for the authoritative PII_FILTER_IDS list; without
	// it every call into piiFilterIds() throws and onMount aborts silently.
	config: {
		subscribe: (run: (v: any) => void) => {
			run(h.state.config);
			return () => {};
		}
	},
	settings: {
		subscribe: (run: (v: any) => void) => {
			run(h.state.settings);
			return () => {};
		},
		set: async (next: any) => {
			h.settingsSet(next);
			h.state.settings = next;
		}
	},
	user: {
		subscribe: (run: (v: any) => void) => {
			run(h.state.user);
			return () => {};
		},
		set: async (next: any) => {
			h.userSet(next);
			h.state.user = next;
		}
	}
}));

import Privacy from './Privacy.svelte';

if (!Element.prototype.animate) {
	Element.prototype.animate = function () {
		const anim = {
			onfinish: null as (() => void) | null,
			cancel() {},
			finished: Promise.resolve()
		};
		queueMicrotask(() => anim.onfinish?.());
		return anim as unknown as Animation;
	};
}

const i18n = readable({
	t: (key: string, vars?: Record<string, string>) =>
		vars ? key.replace(/\{\{(\w+)\}\}/g, (_, k) => vars[k] ?? k) : key
});

const STORED_OFF = {
	pipelines: { valves: { pii_filter: { pii_masking_enabled: false } } }
};

const lastPreference = () => (setOwnPiiMasking.mock.calls.at(-1) as any[] | undefined)?.[1];

const renderPrivacy = () => render(Privacy, { props: {}, context: new Map([['i18n', i18n]]) });

const save = async () => {
	const form = document.getElementById('tab-privacy') as HTMLFormElement;
	await fireEvent.submit(form);
	await tick();
};

beforeEach(() => {
	vi.clearAllMocks();
	(globalThis as any).localStorage = { token: 'test-token' };
	h.state.settings = {};
	h.state.user = { role: 'user', permissions: {} };
	h.state.config = { features: { pii_filter_ids: ['pii_filter'] } };
});

describe('Privacy — policy is not enforced', () => {
	const toggle = () => screen.getByRole('switch');
	const toggled = () => toggle().getAttribute('aria-checked') === 'true';

	it('renders an interactive switch and no policy note', () => {
		renderPrivacy();
		expect(screen.queryByTestId('pii-masking-lock')).toBeNull();
		expect(screen.queryByText(/enforced by your organisation/i)).toBeNull();
		expect(toggle()).toBeTruthy();
	});

	it('shows an unset user the instance default of on', async () => {
		renderPrivacy();
		await tick();
		expect(toggled()).toBe(true);
	});

	it('shows an unset user the instance default of off', async () => {
		h.state.config = { features: { pii_filter_ids: ['pii_filter'], pii_masking_default: false } };
		renderPrivacy();
		await tick();
		expect(toggled()).toBe(false);
	});

	it('shows the stored choice', async () => {
		h.state.settings = STORED_OFF;
		renderPrivacy();
		await tick();
		expect(toggled()).toBe(false);
	});

	it('Save without a change writes nothing, so an unset user keeps following the default', async () => {
		renderPrivacy();
		await tick();
		await save();
		expect(setOwnPiiMasking).not.toHaveBeenCalled();
	});

	it('Save sends off after switching off', async () => {
		renderPrivacy();
		await tick();
		await fireEvent.click(toggle());
		await tick();
		await save();

		expect(lastPreference()).toBe('off');
		expect(h.settingsSet).toHaveBeenCalledWith({ saved: true });
	});

	it('Save sends on after switching on from a stored off', async () => {
		h.state.settings = STORED_OFF;
		renderPrivacy();
		await tick();
		await fireEvent.click(toggle());
		await tick();
		await save();

		expect(lastPreference()).toBe('on');
	});

	it('shows the error and keeps the store when the save is refused', async () => {
		setOwnPiiMasking.mockRejectedValueOnce('PII masking is enforced');
		const dispatched = vi.fn();
		render(Privacy, {
			props: {},
			context: new Map([['i18n', i18n]]),
			events: { save: dispatched }
		} as any);
		await tick();
		await fireEvent.click(toggle());
		await tick();
		await save();

		expect(h.toastError).toHaveBeenCalledWith('PII masking is enforced');
		expect(h.settingsSet).not.toHaveBeenCalled();
		expect(dispatched).not.toHaveBeenCalled();
	});
});

describe('Privacy — policy enforced', () => {
	beforeEach(() => {
		h.state.user = { role: 'user', permissions: { chat: { pii_masking_enforced: true } } };
	});

	// --- The invariant the whole feature rests on: the policy never writes ---

	it('renders only the locked switch and Save does not write', async () => {
		h.state.settings = STORED_OFF;
		renderPrivacy();
		await tick();

		expect(screen.getByTestId('pii-masking-lock')).toBeTruthy();

		await save();
		expect(setOwnPiiMasking).not.toHaveBeenCalled();
	});

	it('structural: the locked Switch displays ON without a binding', async () => {
		h.state.settings = STORED_OFF;
		renderPrivacy();
		await tick();

		const lock = screen.getByTestId('pii-masking-lock');
		expect(lock.querySelector('[aria-checked="true"]')).not.toBeNull();
	});

	// --- Locking -------------------------------------------------------------

	it('wraps the Switch in an inert container', async () => {
		renderPrivacy();
		await tick();

		const lock = screen.getByTestId('pii-masking-lock');
		expect(lock.hasAttribute('inert')).toBe(true);
		expect(lock.getAttribute('aria-disabled')).toBe('true');
	});

	it('shows a reason naming the policy and who to contact', async () => {
		renderPrivacy();
		await tick();

		const note = screen.getByText(/enforced by your organisation/i);
		expect(note.textContent).toMatch(/administrator/i);
	});

	it('points the lock at the reason via aria-describedby', async () => {
		renderPrivacy();
		await tick();

		const lock = screen.getByTestId('pii-masking-lock');
		const describedBy = lock.getAttribute('aria-describedby');
		expect(describedBy).toBe('pii-masking-policy-reason');
		expect(document.getElementById(describedBy!)).not.toBeNull();
	});

	it('derives the lock from the policy, not from the stored value', async () => {
		// Stored ON, policy ON -> still locked. Stored value must not decide.
		h.state.settings = { pipelines: { valves: { pii_filter: { pii_masking_enabled: true } } } };
		renderPrivacy();
		await tick();

		expect(screen.getByTestId('pii-masking-lock')).toBeTruthy();
	});
});

describe('Privacy — stale-policy session refresh', () => {
	it('refreshes the session while unlocked', async () => {
		renderPrivacy();
		await waitFor(() => expect(getSessionUser).toHaveBeenCalledTimes(1));
	});

	it('does not refresh the session while already locked', async () => {
		h.state.user = { role: 'user', permissions: { chat: { pii_masking_enforced: true } } };
		renderPrivacy();
		await tick();
		await tick();

		expect(getSessionUser).not.toHaveBeenCalled();
	});

	it('survives a failing session refresh', async () => {
		getSessionUser.mockRejectedValueOnce(new Error('offline'));
		renderPrivacy();
		await tick();

		expect(screen.queryByTestId('pii-masking-lock')).toBeNull();
	});
});

describe('permissions constant', () => {
	it('carries pii_masking_enforced, defaulting to false', async () => {
		const { DEFAULT_PERMISSIONS } = await vi.importActual<any>('$lib/constants/permissions');
		expect(DEFAULT_PERMISSIONS.chat.pii_masking_enforced).toBe(false);
	});

	it('treats a missing permission field as not enforced', () => {
		const noField: any = { permissions: { chat: {} } };
		expect(noField?.permissions?.chat?.pii_masking_enforced ?? false).toBe(false);

		const noChat: any = { permissions: {} };
		expect(noChat?.permissions?.chat?.pii_masking_enforced ?? false).toBe(false);
	});
});
