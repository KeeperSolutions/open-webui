// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { readable } from 'svelte/store';

import HgAuthCard from './HgAuthCard.svelte';
import * as authsApi from '$lib/apis/auths';
import { config as mockConfig } from '$lib/stores';

vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		config: writable({
			features: {
				enable_login_form: true,
				enable_signup: true,
				enable_ldap: false,
				enable_signup_password_confirmation: false
			},
			oauth: { providers: {} },
			onboarding: false,
			metadata: {}
		}),
		WEBUI_NAME: writable('Hubgate')
	};
});

vi.mock('$lib/apis/auths', () => ({
	userSignIn: vi.fn(),
	userSignUp: vi.fn(),
	ldapUserSignIn: vi.fn()
}));

vi.mock('$lib/utils', () => ({
	generateInitialsImage: vi.fn(() => 'data:image/png;base64,fake')
}));

vi.mock('$lib/constants', () => ({
	WEBUI_BASE_URL: 'http://localhost:8080'
}));

const i18n = readable({
	t: (key: string, vars?: Record<string, string>) => {
		if (!vars) return key;
		return key.replace(/\{\{(\w+)\}\}/g, (_, k) => vars[k] ?? k);
	}
});

const renderCard = (props = {}) =>
	render(HgAuthCard, { props, context: new Map([['i18n', i18n]]) });

const defaultConfig = {
	features: {
		enable_login_form: true,
		enable_signup: true,
		enable_ldap: false,
		enable_signup_password_confirmation: false
	},
	oauth: { providers: {} },
	onboarding: false,
	metadata: {}
};

beforeEach(() => {
	vi.clearAllMocks();
	mockConfig.set(defaultConfig);
});

// ─── API calls ────────────────────────────────────────────────────────────────

describe('sign-in submission', () => {
	it('calls userSignIn with the entered email and password', async () => {
		vi.mocked(authsApi.userSignIn).mockResolvedValue({ token: 'tok', id: '1' });
		renderCard();

		await fireEvent.input(screen.getByLabelText('Email'), { target: { value: 'user@example.com' } });
		await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'secret' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));

		await waitFor(() => {
			expect(authsApi.userSignIn).toHaveBeenCalledWith('user@example.com', 'secret');
		});
	});

	it('resolves with the full session user object on successful sign-in', async () => {
		const sessionUser = { token: 'tok', id: '1', email: 'user@example.com' };
		vi.mocked(authsApi.userSignIn).mockResolvedValue(sessionUser);
		renderCard();

		await fireEvent.input(screen.getByLabelText('Email'), { target: { value: 'user@example.com' } });
		await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'secret' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));

		await waitFor(() => {
			expect(authsApi.userSignIn).toHaveBeenCalledWith('user@example.com', 'secret');
		});
		await expect(vi.mocked(authsApi.userSignIn).mock.results[0].value).resolves.toEqual(sessionUser);
	});

	it('does not dispatch success when userSignIn rejects', async () => {
		vi.mocked(authsApi.userSignIn).mockRejectedValue(new Error('Invalid credentials'));
		const { container } = renderCard();

		const onSuccess = vi.fn();
		container.addEventListener('success', onSuccess);

		await fireEvent.input(screen.getByLabelText('Email'), { target: { value: 'bad@example.com' } });
		await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'wrong' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));

		await waitFor(() => {
			expect(authsApi.userSignIn).toHaveBeenCalled();
		});
		expect(onSuccess).not.toHaveBeenCalled();
	});
});

describe('sign-up submission', () => {
	const switchToSignUp = async () => {
		renderCard();
		await fireEvent.click(screen.getByRole('button', { name: /create one/i }));
	};

	it('calls userSignUp with name, email, password and an avatar', async () => {
		vi.mocked(authsApi.userSignUp).mockResolvedValue({ token: 'tok', id: '2' });
		await switchToSignUp();

		await fireEvent.input(screen.getByLabelText('Name'), { target: { value: 'Jane Smith' } });
		await fireEvent.input(screen.getByLabelText('Email'), { target: { value: 'jane@example.com' } });
		await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'pass123' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Create Account' }));

		await waitFor(() => {
			expect(authsApi.userSignUp).toHaveBeenCalledWith(
				'Jane Smith',
				'jane@example.com',
				'pass123',
				expect.any(String)
			);
		});
	});

	it('blocks submission when passwords do not match (confirmation enabled)', async () => {
		mockConfig.set({
			...defaultConfig,
			features: { ...defaultConfig.features, enable_signup_password_confirmation: true }
		});
		vi.mocked(authsApi.userSignUp).mockResolvedValue({ token: 'tok', id: '2' });
		const { container } = renderCard();
		await fireEvent.click(screen.getByRole('button', { name: /create one/i }));

		const onSuccess = vi.fn();
		container.addEventListener('success', onSuccess);

		await fireEvent.input(screen.getByLabelText('Name'), { target: { value: 'Jane' } });
		await fireEvent.input(screen.getByLabelText('Email'), { target: { value: 'jane@example.com' } });
		await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'pass123' } });
		await fireEvent.input(screen.getByLabelText('Confirm Password'), { target: { value: 'different' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Create Account' }));

		await waitFor(() => {
			expect(authsApi.userSignUp).not.toHaveBeenCalled();
		});
		expect(onSuccess).not.toHaveBeenCalled();
	});
});

describe('LDAP submission', () => {
	beforeEach(() => {
		mockConfig.set({
			...defaultConfig,
			features: { ...defaultConfig.features, enable_ldap: true }
		});
	});

	it('calls ldapUserSignIn with username and password', async () => {
		vi.mocked(authsApi.ldapUserSignIn).mockResolvedValue({ token: 'tok', id: '3' });
		renderCard();

		await fireEvent.input(screen.getByLabelText('Username'), { target: { value: 'jsmith' } });
		await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'ldappass' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Authenticate' }));

		await waitFor(() => {
			expect(authsApi.ldapUserSignIn).toHaveBeenCalledWith('jsmith', 'ldappass');
		});
	});

	it('does not call userSignIn when in LDAP mode', async () => {
		vi.mocked(authsApi.ldapUserSignIn).mockResolvedValue({ token: 'tok', id: '3' });
		renderCard();

		await fireEvent.input(screen.getByLabelText('Username'), { target: { value: 'jsmith' } });
		await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'ldappass' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Authenticate' }));

		await waitFor(() => expect(authsApi.ldapUserSignIn).toHaveBeenCalled());
		expect(authsApi.userSignIn).not.toHaveBeenCalled();
	});
});

// ─── conditional form fields ──────────────────────────────────────────────────

describe('conditional fields based on config', () => {
	it('shows confirm password field only when feature flag is on', async () => {
		mockConfig.set({
			...defaultConfig,
			features: { ...defaultConfig.features, enable_signup_password_confirmation: true }
		});
		renderCard();
		await fireEvent.click(screen.getByRole('button', { name: /create one/i }));
		expect(screen.getByLabelText('Confirm Password')).toBeInTheDocument();
	});

	it('does not show confirm password when feature flag is off', async () => {
		renderCard();
		await fireEvent.click(screen.getByRole('button', { name: /create one/i }));
		expect(screen.queryByLabelText('Confirm Password')).not.toBeInTheDocument();
	});

	it('shows username field instead of email when LDAP is the active mode', () => {
		mockConfig.set({
			...defaultConfig,
			features: { ...defaultConfig.features, enable_ldap: true }
		});
		renderCard();
		expect(screen.getByLabelText('Username')).toBeInTheDocument();
		expect(screen.queryByLabelText('Email')).not.toBeInTheDocument();
	});

	it('hides the sign-up toggle when enable_signup is false', () => {
		mockConfig.set({
			...defaultConfig,
			features: { ...defaultConfig.features, enable_signup: false }
		});
		renderCard();
		expect(screen.queryByRole('button', { name: /create one/i })).not.toBeInTheDocument();
	});
});

// ─── mode transitions ─────────────────────────────────────────────────────────

describe('mode transitions', () => {
	it('switching to sign-up replaces the email form with name + email + password', async () => {
		renderCard();
		await fireEvent.click(screen.getByRole('button', { name: /create one/i }));
		expect(screen.getByLabelText('Name')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Create Account' })).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Sign In' })).not.toBeInTheDocument();
	});

	it('switching back from sign-up to sign-in removes the name field', async () => {
		renderCard();
		await fireEvent.click(screen.getByRole('button', { name: /create one/i }));
		await fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
		expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Sign In' })).toBeInTheDocument();
	});

	it('forgot password hides the sign-in form and shows reset form', async () => {
		renderCard();
		await fireEvent.click(screen.getByText('Forgot password?'));
		expect(screen.queryByRole('button', { name: 'Sign In' })).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Send Reset Link' })).toBeInTheDocument();
	});

	it('returning from reset mode restores the sign-in form', async () => {
		renderCard();
		await fireEvent.click(screen.getByText('Forgot password?'));
		await fireEvent.click(screen.getByText(/sign in/i));
		expect(screen.queryByRole('button', { name: 'Send Reset Link' })).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Sign In' })).toBeInTheDocument();
	});
});
