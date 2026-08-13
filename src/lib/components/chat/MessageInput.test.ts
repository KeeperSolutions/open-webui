// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import { readable, writable } from 'svelte/store';

// ── Mock SvelteKit app modules (avoid loading the real client runtime) ─────────
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/environment', () => ({ browser: true, dev: false, building: false }));
vi.mock('$app/stores', () => ({
	page: {
		subscribe: (fn: (v: { url: { pathname: string } }) => void) => {
			fn({ url: { pathname: '/' } });
			return () => {};
		}
	}
}));

// ── Mock stores ───────────────────────────────────────────────────────────────
vi.mock('$lib/stores', () => ({
	mobile: writable(false),
	settings: writable({}),
	models: writable([]),
	config: writable({ audio: { stt: { engine: '' }, tts: { engine: '' } } }),
	showCallOverlay: writable(false),
	tools: writable([]),
	toolServers: writable([]),
	terminalServers: writable([]),
	selectedTerminalId: writable(null),
	user: writable({ role: 'user', permissions: {} }),
	showControls: writable(false),
	showSettings: writable(false),
	TTSWorker: writable(null),
	temporaryChatEnabled: writable(false),
	theme: writable('light')
}));

// ── Mock APIs ─────────────────────────────────────────────────────────────────
vi.mock('$lib/apis/files', () => ({ uploadFile: vi.fn() }));
vi.mock('$lib/apis', () => ({ generateAutoCompletion: vi.fn() }));
vi.mock('$lib/apis/auths', () => ({ getSessionUser: vi.fn() }));
vi.mock('$lib/apis/tools', () => ({ getTools: vi.fn(async () => []) }));

// ── Mock heavy / browser-only deps ────────────────────────────────────────────
vi.mock('$lib/utils/google-drive-picker', () => ({
	createPicker: vi.fn(),
	getAuthToken: vi.fn()
}));
vi.mock('$lib/utils/onedrive-file-picker', () => ({ pickAndDownloadFile: vi.fn() }));
vi.mock('$lib/workers/KokoroWorker', () => ({ KokoroWorker: class {} }));

// ── Stub heavy child components (TipTap editor, voice, modals) ─────────────────
// RichTextInput mounts a TipTap editor that does not work in jsdom; stub it.
vi.mock('../common/RichTextInput.svelte', async () => ({
	default: (await import('./__stubs__/EmptyComponent.svelte')).default
}));
vi.mock('./MessageInput/VoiceRecording.svelte', async () => ({
	default: (await import('./__stubs__/EmptyComponent.svelte')).default
}));

import MessageInput from './MessageInput.svelte';

const i18n = readable({ t: (k: string) => k });

const renderInput = (overrides = {}) =>
	render(MessageInput, {
		props: {
			createMessagePair: vi.fn(),
			stopResponse: vi.fn(),
			selectedModels: [''],
			history: { currentId: null, messages: {} },
			prompt: '',
			files: [],
			generating: false,
			piiMaskingEnabled: true,
			...overrides
		},
		context: new Map([['i18n', i18n]])
	});

beforeEach(() => {
	vi.clearAllMocks();
	Object.defineProperty(window, 'matchMedia', {
		writable: true,
		value: vi
			.fn()
			.mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })
	});
});

const getPiiToggle = () => screen.getByRole('switch', { name: /toggle pii masking/i });

describe('MessageInput — PII masking toggle', () => {
	it('renders the toggle when idle (not generating, no in-flight message)', () => {
		renderInput();
		expect(getPiiToggle()).toBeInTheDocument();
	});

	it('reflects piiMaskingEnabled=true via aria-checked', () => {
		renderInput({ piiMaskingEnabled: true });
		expect(getPiiToggle()).toHaveAttribute('aria-checked', 'true');
	});

	it('reflects piiMaskingEnabled=false via aria-checked', () => {
		renderInput({ piiMaskingEnabled: false });
		expect(getPiiToggle()).toHaveAttribute('aria-checked', 'false');
	});

	it('clicking flips aria-checked from true to false', async () => {
		renderInput({ piiMaskingEnabled: true });
		await fireEvent.click(getPiiToggle());
		expect(getPiiToggle()).toHaveAttribute('aria-checked', 'false');
	});

	it('clicking flips aria-checked from false to true', async () => {
		renderInput({ piiMaskingEnabled: false });
		await fireEvent.click(getPiiToggle());
		expect(getPiiToggle()).toHaveAttribute('aria-checked', 'true');
	});

	it('is not rendered while generating (stop button shown instead)', () => {
		renderInput({ generating: true });
		expect(screen.queryByRole('switch', { name: /toggle pii masking/i })).toBeNull();
		expect(screen.getByRole('button', { name: /stop/i })).toBeInTheDocument();
	});
});

describe('MessageInput — PII masking toggle under team policy', () => {
	it('is disabled when the policy enforces masking', () => {
		renderInput({ piiMaskingLocked: true });
		expect(getPiiToggle()).toBeDisabled();
	});

	it('displays ON even when the user’s own value is off', () => {
		renderInput({ piiMaskingLocked: true, piiMaskingEnabled: false });
		expect(getPiiToggle()).toHaveAttribute('aria-checked', 'true');
	});

	it('does not flip when clicked while locked', async () => {
		renderInput({ piiMaskingLocked: true, piiMaskingEnabled: false });
		await fireEvent.click(getPiiToggle());
		expect(getPiiToggle()).toHaveAttribute('aria-checked', 'true');
	});

	it('derives the lock from the policy prop, not from the user’s value', () => {
		// User value ON, no policy -> must NOT be locked. Proves the lock is not
		// being read off the masking value itself.
		renderInput({ piiMaskingLocked: false, piiMaskingEnabled: true });
		expect(getPiiToggle()).not.toBeDisabled();
	});

	it('stays interactive and reflects the user’s value when no policy applies', () => {
		renderInput({ piiMaskingLocked: false, piiMaskingEnabled: false });
		const toggle = getPiiToggle();
		expect(toggle).not.toBeDisabled();
		expect(toggle).toHaveAttribute('aria-checked', 'false');
	});

	it('⚠️ never writes the policy value back into the user’s own value', async () => {
		// The Z-1 invariant at its second site. `piiMaskingEnabled` is what the
		// parent binds and what ends up in the draft; the policy must only ever
		// layer on top of it for display. If the lock were folded INTO that
		// variable, the component would emit `true` here.
		const emitted: boolean[] = [];
		renderInput({
			piiMaskingLocked: true,
			piiMaskingEnabled: false,
			onChange: (d: { piiMaskingEnabled: boolean }) => emitted.push(d.piiMaskingEnabled)
		});

		await fireEvent.click(getPiiToggle());

		expect(getPiiToggle()).toHaveAttribute('aria-checked', 'true');
		expect(emitted.length).toBeGreaterThan(0);
		expect(emitted.every((v) => v === false)).toBe(true);
	});
});
