<script lang="ts">
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';
	import { createEventDispatcher, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { ldapUserSignIn, userSignIn, userSignUp } from '$lib/apis/auths';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { WEBUI_NAME, config } from '$lib/stores';
	import { generateInitialsImage } from '$lib/utils';

	import HgButton from './HgButton.svelte';
	import HgInput from './HgInput.svelte';
	import HgSSOButton from './HgSSOButton.svelte';
	import HgDivider from './HgDivider.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let form: string | null = null;

	let mode: string = $config?.features.enable_ldap ? 'ldap' : 'signin';

	let name = '';
	let email = '';
	let password = '';
	let confirmPassword = '';
	let ldapUsername = '';

	const signInHandler = async () => {
		const sessionUser = await userSignIn(email, password).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (sessionUser) dispatch('success', sessionUser);
	};

	const signUpHandler = async () => {
		if ($config?.features?.enable_signup_password_confirmation) {
			if (password !== confirmPassword) {
				toast.error($i18n.t('Passwords do not match.'));
				return;
			}
		}
		const sessionUser = await userSignUp(name, email, password, generateInitialsImage(name)).catch(
			(error) => {
				toast.error(`${error}`);
				return null;
			}
		);
		if (sessionUser) dispatch('success', sessionUser);
	};

	const ldapSignInHandler = async () => {
		const sessionUser = await ldapUserSignIn(ldapUsername, password).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (sessionUser) dispatch('success', sessionUser);
	};

	const submitHandler = async () => {
		if (mode === 'ldap') {
			await ldapSignInHandler();
		} else if (mode === 'signin') {
			await signInHandler();
		} else {
			await signUpHandler();
		}
	};
</script>

<div
	class="bg-hg-bg-surface rounded-hg-md shadow-sm flex flex-col justify-center items-center gap-6 p-6 sm:p-8 w-full sm:w-[480px]"
>
	<div class="w-full flex flex-col items-center gap-3">
		<img src="/hubgate/hubgate-logomark.svg" class="size-12" alt="Hubgate" />

		{#if mode === 'reset'}
			<div class="text-center">
				<h1 class="font-hg-heading font-bold text-xl text-hg-text-primary leading-tight">
					{$i18n.t('Reset your password')}
				</h1>
				<p class="mt-1 text-sm text-hg-text-secondary font-hg-body">
					{$i18n.t("Enter your email and we'll send you a link")}
				</p>
			</div>
		{:else if mode === 'signup'}
			<div class="text-center">
				<h1 class="font-hg-heading font-bold text-xl text-hg-text-primary leading-tight">
					{($config?.onboarding ?? false)
						? $i18n.t('Get started with {{WEBUI_NAME}}', { WEBUI_NAME: $WEBUI_NAME })
						: $i18n.t('Try {{WEBUI_NAME}} for free', { WEBUI_NAME: $WEBUI_NAME })}
				</h1>
				<p class="mt-1 text-sm text-hg-text-secondary font-hg-body">
					{$i18n.t(
						'Get a €2.00 free credit to explore leading AI models. No credit card required.'
					)}
				</p>
			</div>
		{:else}
			<div class="text-center">
				<h1 class="font-hg-heading font-bold text-xl text-hg-text-primary leading-tight">
					{$i18n.t('Welcome back')}
				</h1>
				<p class="mt-1 text-sm text-hg-text-secondary font-hg-body">
					{mode === 'ldap'
						? $i18n.t('Sign in to your {{WEBUI_NAME}} account with LDAP', {
								WEBUI_NAME: $WEBUI_NAME
							})
						: $i18n.t('Sign in to your {{WEBUI_NAME}} account', { WEBUI_NAME: $WEBUI_NAME })}
				</p>
			</div>
		{/if}
	</div>

	{#if mode === 'reset'}
		<form
			class="flex flex-col gap-4"
			on:submit|preventDefault={() => {
				toast.info($i18n.t('Password reset is not available yet.'));
			}}
		>
			<HgInput
				label={$i18n.t('Email')}
				type="email"
				name="email"
				placeholder="jane@company.com"
				bind:value={email}
				required
			/>
			<HgButton variant="primary" size="lg" type="submit" fullWidth>
				{$i18n.t('Send Reset Link')}
			</HgButton>
		</form>
		<p class="text-center text-sm text-hg-text-secondary font-hg-body">
			{$i18n.t('Remember your password?')}
			<button
				type="button"
				class="text-hg-blue font-medium hover:underline ml-1"
				on:click={() => (mode = 'signin')}
			>
				{$i18n.t('Sign in')} ›
			</button>
		</p>
	{:else}
		{#if Object.keys($config?.oauth?.providers ?? {}).length > 0}
			<div class="w-full flex flex-col gap-3">
				{#if $config?.oauth?.providers?.google}
					<HgSSOButton provider="google" href={`${WEBUI_BASE_URL}/oauth/google/login`} />
				{/if}
				<!-- Temporarily disable Microsoft SSO -->
				<!-- {#if $config?.oauth?.providers?.microsoft}
					<HgSSOButton provider="microsoft" href={`${WEBUI_BASE_URL}/oauth/microsoft/login`} />
				{/if} -->
				{#if $config?.oauth?.providers?.github}
					<button
						type="button"
						class="w-full h-12 inline-flex items-center justify-center gap-3 font-hg-body font-medium text-sm text-hg-text-primary bg-hg-bg-surface hover:bg-hg-bg-muted border border-hg-border rounded-hg-full transition-colors duration-150"
						on:click={() => {
							window.location.href = `${WEBUI_BASE_URL}/oauth/github/login`;
						}}
					>
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 24 24"
							class="size-5 shrink-0"
							aria-hidden="true"
						>
							<path
								fill="currentColor"
								d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.92 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57C20.565 21.795 24 17.31 24 12c0-6.63-5.37-12-12-12z"
							/>
						</svg>
						<span>{$i18n.t('Continue with {{provider}}', { provider: 'GitHub' })}</span>
					</button>
				{/if}
				{#if $config?.oauth?.providers?.oidc}
					<button
						type="button"
						class="w-full h-12 inline-flex items-center justify-center gap-3 font-hg-body font-medium text-sm text-hg-text-primary bg-hg-bg-surface hover:bg-hg-bg-muted border border-hg-border rounded-hg-full transition-colors duration-150"
						on:click={() => {
							window.location.href = `${WEBUI_BASE_URL}/oauth/oidc/login`;
						}}
					>
						<span
							>{$i18n.t('Continue with {{provider}}', {
								provider: $config?.oauth?.providers?.oidc ?? 'SSO'
							})}</span
						>
					</button>
				{/if}
			</div>

			{#if $config?.features.enable_login_form || $config?.features.enable_ldap || form}
				<HgDivider />
			{/if}
		{/if}

		{#if $config?.features.enable_login_form || $config?.features.enable_ldap || form}
			<form class="w-full flex flex-col gap-4" on:submit|preventDefault={submitHandler}>
				{#if mode === 'signup'}
					<HgInput
						label={$i18n.t('Name')}
						type="text"
						name="name"
						placeholder={$i18n.t('Jane Smith')}
						autocomplete="name"
						bind:value={name}
						required
					/>
				{/if}

				{#if mode === 'ldap'}
					<HgInput
						label={$i18n.t('Username')}
						type="text"
						name="username"
						placeholder={$i18n.t('Enter Your Username')}
						autocomplete="username"
						bind:value={ldapUsername}
						required
					/>
				{:else}
					<HgInput
						label={$i18n.t('Email')}
						type="email"
						name="email"
						placeholder="jane@company.com"
						autocomplete="email"
						bind:value={email}
						required
					/>
				{/if}

				<div class="flex flex-col gap-1">
					<HgInput
						label={$i18n.t('Password')}
						type="password"
						name="password"
						placeholder={$i18n.t('Enter your password')}
						autocomplete={mode === 'signup' ? 'new-password' : 'current-password'}
						bind:value={password}
						required
					/>
					{#if mode === 'signin'}
						<div class="flex justify-end">
							<button
								type="button"
								class="text-xs text-hg-text-secondary hover:text-hg-text-primary font-hg-body"
								on:click={() => (mode = 'reset')}
							>
								{$i18n.t('Forgot password?')}
							</button>
						</div>
					{/if}
				</div>

				{#if mode === 'signup' && $config?.features?.enable_signup_password_confirmation}
					<HgInput
						label={$i18n.t('Confirm Password')}
						type="password"
						name="confirm-password"
						placeholder={$i18n.t('Confirm Your Password')}
						autocomplete="new-password"
						bind:value={confirmPassword}
						required
					/>
				{/if}

				{#if mode === 'ldap'}
					<HgButton variant="primary" size="lg" type="submit" fullWidth>
						{$i18n.t('Authenticate')}
					</HgButton>
				{:else}
					<HgButton variant="primary" size="lg" type="submit" fullWidth>
						{mode === 'signin'
							? $i18n.t('Sign In')
							: ($config?.onboarding ?? false)
								? $i18n.t('Create Admin Account')
								: $i18n.t('Create Account')}
					</HgButton>
				{/if}
			</form>
		{/if}

		{#if $config?.features.enable_ldap && $config?.features.enable_login_form}
			<div class="text-center">
				<button
					type="button"
					class="text-xs text-hg-text-secondary hover:text-hg-text-primary underline font-hg-body"
					on:click={() => {
						if (mode === 'ldap') mode = ($config?.onboarding ?? false) ? 'signup' : 'signin';
						else mode = 'ldap';
					}}
				>
					{mode === 'ldap' ? $i18n.t('Continue with Email') : $i18n.t('Continue with LDAP')}
				</button>
			</div>
		{/if}

		{#if $config?.features.enable_signup && !($config?.onboarding ?? false) && mode !== 'ldap'}
			<p class="text-center text-sm text-hg-text-secondary font-hg-body">
				{mode === 'signin'
					? $i18n.t("Don't have an account?")
					: $i18n.t('Already have an account?')}
				<button
					type="button"
					class="text-hg-blue font-medium hover:underline ml-1"
					on:click={() => {
						mode = mode === 'signin' ? 'signup' : 'signin';
					}}
				>
					{mode === 'signin' ? $i18n.t('Create one') : $i18n.t('Sign in')}
					{#if mode === 'signin'}›{/if}
				</button>
			</p>
		{/if}
	{/if}

	{#if $config?.metadata?.login_footer}
		<div class="text-[0.7rem] text-hg-text-tertiary font-hg-body marked">
			{@html DOMPurify.sanitize(marked($config?.metadata?.login_footer))}
		</div>
	{/if}
</div>
