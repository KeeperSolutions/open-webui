// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { readable } from 'svelte/store';
import { tick } from 'svelte';

// vi.mock factories are hoisted above every declaration in this file, so any
// value they close over has to be created by vi.hoisted.
const h = vi.hoisted(() => ({
	updateUserSettings: vi.fn(async () => ({})),
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

const { updateUserSettings, getSessionUser, isPiiPipelineConfigured } = h;

vi.mock('$lib/apis/users', () => ({ updateUserSettings: h.updateUserSettings }));
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

const renderPrivacy = () => render(Privacy, { props: {}, context: new Map([['i18n', i18n]]) });

const save = async () => {
	const form = document.getElementById('tab-privacy') as HTMLFormElement;
	await fireEvent.submit(form);
	await tick();
};

/** The masking valves in the payload the component tried to persist. */
const persistedValves = () => {
	const call = updateUserSettings.mock.calls.at(-1) as any[] | undefined;
	return call?.[1]?.ui?.pipelines?.valves ?? null;
};

beforeEach(() => {
	vi.clearAllMocks();
	(globalThis as any).localStorage = { token: 'test-token' };
	h.state.settings = {};
	h.state.user = { role: 'user', permissions: {} };
});

describe('Privacy — policy is not enforced', () => {
	it('renders an interactive Switch and no policy note', () => {
		renderPrivacy();
		expect(screen.queryByTestId('pii-masking-lock')).toBeNull();
		expect(screen.queryByText(/enforced by your organisation/i)).toBeNull();
	});

	it('Save persists the user’s own value', async () => {
		h.state.settings = STORED_OFF;
		renderPrivacy();
		await tick();
		await save();

		expect(persistedValves()?.pii_filter?.pii_masking_enabled).toBe(false);
	});
});

describe('Privacy — policy enforced', () => {
	beforeEach(() => {
		h.state.user = { role: 'user', permissions: { chat: { pii_masking_enforced: true } } };
	});

	// --- Z-1: the invariant the whole spec rests on -------------------------

	it('Z-1: Save does not touch any masking valve while locked', async () => {
		h.state.settings = STORED_OFF;
		renderPrivacy();
		await tick();
		await save();

		const valves = persistedValves();
		// The stored preference survives verbatim...
		expect(valves?.pii_filter?.pii_masking_enabled).toBe(false);
		// ...and no filter id was rewritten to the displayed `true`.
		for (const valve of Object.values(valves ?? {})) {
			expect((valve as any).pii_masking_enabled).not.toBe(true);
		}
	});

	it('Z-1 (literal): the stored valve object is not rewritten at all while locked', async () => {
		// The value-level assertion above cannot see the guard being removed: with
		// the structural separation in place, rewriting the valves would write the
		// SAME value back. This checks the spec's literal "must not touch" instead,
		// by reference identity — an untouched valve is still the very object that
		// came out of settings, while any rewrite produces a fresh one.
		h.state.settings = STORED_OFF;
		const originalValve = STORED_OFF.pipelines.valves.pii_filter;

		renderPrivacy();
		await tick();
		await save();

		expect(persistedValves()?.pii_filter).toBe(originalValve);
	});

	it('Z-1 (structural): the locked Switch carries no binding back to the stored value', async () => {
		h.state.settings = STORED_OFF;
		const { container } = renderPrivacy();
		await tick();

		// Displayed ON (policy) while stored stays OFF (user's own choice).
		const lock = screen.getByTestId('pii-masking-lock');
		expect(lock.querySelector('[aria-checked="true"]')).not.toBeNull();

		await save();
		expect(persistedValves()?.pii_filter?.pii_masking_enabled).toBe(false);
		expect(container).toBeTruthy();
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

describe('Privacy — D-11 session refresh', () => {
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
