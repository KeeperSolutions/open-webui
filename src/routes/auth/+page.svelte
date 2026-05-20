<script lang="ts">
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { getBackendConfig } from '$lib/apis';
	import {
		ldapUserSignIn,
		getSessionUser,
		userSignIn,
		userSignUp,
		updateUserTimezone
	} from '$lib/apis/auths';

	import { WEBUI_BASE_URL } from '$lib/constants';
	import { WEBUI_NAME, config, user, socket } from '$lib/stores';
	import { generateInitialsImage, getUserTimezone } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import OnBoarding from '$lib/components/OnBoarding.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	const i18n = getContext('i18n');

	let loaded = false;
	let mode = $config?.features.enable_ldap ? 'ldap' : 'signin';
	let form: string | null = null;

	let name = '';
	let email = '';
	let password = '';
	let confirmPassword = '';
	let ldapUsername = '';
	let onboarding = false;

	// Gateway layout state
	const models = [
		{ id: 'claude', name: 'Claude', variant: 'Sonnet 4.6', tags: ['Writing', 'Analysis', 'Precision'], logo: '/static/claude-logo.svg' },
		{ id: 'gemini', name: 'Gemini', variant: '2.5 Pro', tags: ['Big Data', 'Multimodal', 'Deep Analysis'], logo: '/static/gemini-logo.png' },
		{ id: 'gpt4o', name: 'GPT-4o', variant: '', tags: ['Coding', 'General', 'Fast'], logo: '/static/openai-logo.svg' },
		{ id: 'perplexity', name: 'Perplexity', variant: 'Sonar', tags: ['Search', 'Citations', 'Real-Time'], logo: '/static/perplexity-logo.png' }
	];

	const categoryTabs = ['All', 'Writing', 'Coding', 'Analysis', 'Research', 'Images'];

	const suggestedPrompts = [
		{ label: 'Review compliance doc', tooltip: 'Prepare a security or compliance summary from your uploaded docs' },
		{ label: 'Draft a proposal', tooltip: 'Write a client proposal using your company knowledge' },
		{ label: 'Generate test cases', tooltip: 'Create QA test cases from a Jira ticket or transcript' },
		{ label: 'Summarize meeting', tooltip: 'Extract key decisions and action items from a meeting transcript' }
	];

	const carouselSlides = [
		{ title: 'Your data stays yours', description: 'No prompts or responses are used to train AI models. Everything is logged for your audit trail only.', cta: 'Learn more', color: '#10B981', icon: 'shield' },
		{ title: 'Knowledge Bases', description: "Ground every answer in your company's knowledge. Upload docs or connect Confluence.", cta: 'Explore Knowledge', color: '#10B981', icon: 'files' },
		{ title: 'Custom Agents', description: 'Deploy QA Reviewer, Sales Drafter or Legal Counsel — ready in minutes.', cta: 'See Agents', color: '#8B5CF6', icon: 'robot' },
		{ title: 'Audit Log', description: 'Every prompt logged. Built for compliance and security reviews.', cta: 'Learn more', color: '#F59E0B', icon: 'clock' },
		{ title: '4 AI Models', description: 'Claude, Gemini, GPT-4o and Perplexity — one interface.', cta: 'See models', color: '#3B82F6', icon: 'sparkle' },
		{ title: 'Confluence', description: 'Connect your Confluence workspace. AI answers from real docs.', cta: 'Connect', color: '#0052CC', icon: 'confluence' }
	];

	let selectedModel = models[0];
	let isDropdownOpen = false;
	let inputValue = '';
	let isLoginModalOpen = false;
	let modelSearch = '';
	let activeTab = 'All';
	let isChatWidgetOpen = false;
	let carouselIndex = 0;
	let carouselPaused = false;
	let isCarouselVisible = true;

	$: filteredModels = models.filter((m) => {
		if (modelSearch && !m.name.toLowerCase().includes(modelSearch.toLowerCase())) return false;
		if (activeTab === 'All') return true;
		if (activeTab === 'Writing') return m.tags.includes('Writing');
		if (activeTab === 'Coding') return m.tags.includes('Coding');
		if (activeTab === 'Analysis') return m.tags.includes('Analysis') || m.tags.includes('Deep Analysis');
		if (activeTab === 'Research') return m.tags.includes('Deep Analysis') || m.tags.includes('Search') || m.tags.includes('Real-Time');
		if (activeTab === 'Images') return m.tags.includes('Multimodal');
		return true;
	});

	const openLoginModal = () => {
		isLoginModalOpen = true;
		isDropdownOpen = false;
	};

	const handleChatSubmit = () => {
		if (inputValue.trim()) openLoginModal();
	};

	// Auth logic
	const setSessionUser = async (sessionUser: any, redirectPath: string | null = null) => {
		if (sessionUser) {
			toast.success($i18n.t(`You're now logged in.`));
			if (sessionUser.token) localStorage.token = sessionUser.token;
			$socket.emit('user-join', { auth: { token: sessionUser.token } });
			await user.set(sessionUser);
			await config.set(await getBackendConfig());
			const timezone = getUserTimezone();
			if (sessionUser.token && timezone) updateUserTimezone(sessionUser.token, timezone);
			if (!redirectPath) redirectPath = $page.url.searchParams.get('redirect') || '/';
			goto(redirectPath);
			localStorage.removeItem('redirectPath');
		}
	};

	const signInHandler = async () => {
		const sessionUser = await userSignIn(email, password).catch((error) => { toast.error(`${error}`); return null; });
		await setSessionUser(sessionUser);
	};

	const signUpHandler = async () => {
		if ($config?.features?.enable_signup_password_confirmation && password !== confirmPassword) {
			toast.error($i18n.t('Passwords do not match.'));
			return;
		}
		const sessionUser = await userSignUp(name, email, password, generateInitialsImage(name)).catch((error) => { toast.error(`${error}`); return null; });
		await setSessionUser(sessionUser);
	};

	const ldapSignInHandler = async () => {
		const sessionUser = await ldapUserSignIn(ldapUsername, password).catch((error) => { toast.error(`${error}`); return null; });
		await setSessionUser(sessionUser);
	};

	const submitHandler = async () => {
		if (mode === 'ldap') await ldapSignInHandler();
		else if (mode === 'signin') await signInHandler();
		else await signUpHandler();
	};

	const oauthCallbackHandler = async () => {
		function getCookie(name: string) {
			const match = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1') + '=([^;]*)'));
			return match ? decodeURIComponent(match[1]) : null;
		}
		const token = getCookie('token');
		if (!token) return;
		const sessionUser = await getSessionUser(token).catch((error) => { toast.error(`${error}`); return null; });
		if (!sessionUser) return;
		localStorage.token = token;
		await setSessionUser(sessionUser, localStorage.getItem('redirectPath') || null);
	};

	onMount(async () => {
		const redirectPath = $page.url.searchParams.get('redirect');
		if ($user !== undefined) { goto(redirectPath || '/'); return; }
		if (redirectPath) localStorage.setItem('redirectPath', redirectPath);
		const error = $page.url.searchParams.get('error');
		if (error) toast.error(error);
		await oauthCallbackHandler();
		form = $page.url.searchParams.get('form');
		loaded = true;
		if (($config?.features.auth_trusted_header ?? false) || $config?.features.auth === false) {
			await signInHandler();
		} else {
			onboarding = $config?.onboarding ?? false;
		}
	});

	onMount(() => {
		const interval = setInterval(() => {
			if (!carouselPaused && isCarouselVisible) {
				carouselIndex = (carouselIndex + 1) % carouselSlides.length;
			}
		}, 4500);
		return () => clearInterval(interval);
	});
</script>

<svelte:head>
	<title>{$WEBUI_NAME}</title>
</svelte:head>

<OnBoarding
	bind:show={onboarding}
	getStartedHandler={() => {
		onboarding = false;
		mode = $config?.features.enable_ldap ? 'ldap' : 'signup';
		isLoginModalOpen = true;
	}}
/>

{#if loaded}
	{#if ($config?.features.auth_trusted_header ?? false) || $config?.features.auth === false}
		<div class="flex h-screen items-center justify-center bg-white">
			<div class="flex items-center gap-3 text-xl font-medium text-gray-800">
				{$i18n.t('Signing in to {{WEBUI_NAME}}', { WEBUI_NAME: $WEBUI_NAME })}
				<Spinner className="size-5" />
			</div>
		</div>
	{:else}
		<!-- Gateway Layout -->
		<div class="flex h-screen overflow-hidden bg-white">

			<!-- Left Sidebar -->
			<aside class="flex w-[60px] flex-shrink-0 flex-col items-center border-r border-[#E5E5E5] bg-white py-4">
				<a href="/" class="mb-8 text-lg font-bold leading-none">
					<span style="color:#1E1B4B;">h</span><span style="color:#6366F1;">g</span>
				</a>
				<nav class="flex flex-1 flex-col items-center gap-2">
					<!-- New Chat -->
					<button type="button" title="New Chat" class="flex h-10 w-10 items-center justify-center rounded-lg text-[#666] transition-colors hover:bg-[#F5F5F5] hover:text-[#1A1A2E]">
						<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
							<path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.832 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487z" />
						</svg>
					</button>
					<!-- Search -->
					<button type="button" title="Search" class="flex h-10 w-10 items-center justify-center rounded-lg text-[#666] transition-colors hover:bg-[#F5F5F5] hover:text-[#1A1A2E]">
						<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
							<path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
						</svg>
					</button>
					<!-- Knowledge -->
					<button type="button" title="Knowledge" class="flex h-10 w-10 items-center justify-center rounded-lg text-[#666] transition-colors hover:bg-[#F5F5F5] hover:text-[#1A1A2E]">
						<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
							<path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
						</svg>
					</button>
					<!-- Agents -->
					<button type="button" title="Agents" class="flex h-10 w-10 items-center justify-center rounded-lg text-[#666] transition-colors hover:bg-[#F5F5F5] hover:text-[#1A1A2E]">
						<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
							<path stroke-linecap="round" stroke-linejoin="round" d="M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9" />
						</svg>
					</button>
				</nav>
				<div class="mt-auto">
					<button type="button" aria-label="User profile" class="flex h-9 w-9 items-center justify-center rounded-full bg-[#6366F1] text-white">
						<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
						</svg>
					</button>
				</div>
			</aside>

			<!-- Main Content -->
			<main class="flex flex-1 flex-col overflow-hidden" style="background: linear-gradient(180deg, #FFFFFF 0%, #FAFBFC 50%, #F5F7F9 100%);">

				<!-- Top Bar -->
				<header class="flex h-14 flex-shrink-0 items-center justify-between border-b border-[#E5E5E5] bg-white px-5">
					<!-- Model Selector -->
					<div class="relative">
						<button
							type="button"
							on:click={() => { isDropdownOpen = !isDropdownOpen; }}
							class="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-[#1A1A2E] transition-colors hover:bg-[#F5F5F5]"
						>
							<img src={selectedModel.logo} alt={selectedModel.name} class="h-5 w-5 object-contain" />
							<span class="font-semibold">{selectedModel.name}</span>
							{#if selectedModel.variant}
								<span class="text-[#999]">—</span>
								<span class="font-normal text-[#999]">{selectedModel.variant}</span>
							{/if}
							<svg xmlns="http://www.w3.org/2000/svg" class="ml-0.5 h-3.5 w-3.5 text-[#999]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
								<path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
							</svg>
						</button>

						{#if isDropdownOpen}
							<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
							<div class="fixed inset-0" style="z-index:9998;" on:click={() => { isDropdownOpen = false; modelSearch = ''; }}></div>
							<div class="fixed left-4 top-14 mt-1.5 max-h-[420px] w-96 overflow-y-auto rounded-xl border border-[#E5E5E5] bg-white shadow-xl" style="z-index:99999;">
								<!-- Search -->
								<div class="sticky top-0 border-b border-[#E5E5E5] bg-white p-3">
									<div class="flex items-center gap-2 rounded-lg border border-[#E5E5E5] bg-[#FAFAFA] px-3 py-2">
										<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-[#999]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
											<path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
										</svg>
										<input type="text" bind:value={modelSearch} placeholder="Search a model..." class="flex-1 bg-transparent text-sm text-[#1A1A2E] outline-none placeholder:text-[#999]" />
									</div>
								</div>
								<!-- Tabs -->
								<div class="flex flex-nowrap gap-1.5 overflow-x-auto p-3">
									{#each categoryTabs as tab}
										<button
											type="button"
											on:click={() => activeTab = tab}
											class="flex-shrink-0 rounded-full px-3 py-1 text-xs font-medium transition-colors"
											style={activeTab === tab ? 'background:#1E1B4B;color:white;' : 'background:#F5F5F5;color:#666;'}
										>{tab}</button>
									{/each}
								</div>
								<!-- Models -->
								<div class="p-2 pt-0">
									<div class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-[#999]">AI Models</div>
									{#each filteredModels as model}
										<button
											type="button"
											on:click={() => { selectedModel = model; isDropdownOpen = false; }}
											class="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-[#FAFAFA]"
											style={selectedModel.id === model.id ? 'background:#FAFAFA;' : ''}
										>
											<img src={model.logo} alt={model.name} class="h-6 w-6 object-contain" />
											<div class="flex-1">
												<div class="flex items-center gap-2">
													<span class="text-sm font-medium text-[#1A1A2E]">{model.name}</span>
													{#if model.variant}
														<span class="text-sm text-[#999]">—</span>
														<span class="text-sm text-[#999]">{model.variant}</span>
													{/if}
												</div>
												<div class="mt-1 flex flex-wrap gap-1">
													{#each model.tags as tag}
														<span class="rounded bg-[#F5F5F5] px-1.5 py-0.5 text-[10px] text-[#888]">{tag}</span>
													{/each}
												</div>
											</div>
											{#if selectedModel.id === model.id}
												<span class="text-base font-semibold text-[#6366F1]">✓</span>
											{/if}
										</button>
									{/each}
								</div>
								<!-- Footer note -->
								<div class="border-t border-[#E5E5E5] p-2">
									<div class="flex items-center justify-center gap-1.5 py-2 text-xs text-[#999]">
										<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
											<path fill-rule="evenodd" d="M12 1.5a5.25 5.25 0 00-5.25 5.25v3a3 3 0 00-3 3v6.75a3 3 0 003 3h10.5a3 3 0 003-3v-6.75a3 3 0 00-3-3v-3c0-2.9-2.35-5.25-5.25-5.25zm3.75 8.25v-3a3.75 3.75 0 10-7.5 0v3h7.5z" clip-rule="evenodd" />
										</svg>
										No data used to train AI models
									</div>
								</div>
							</div>
						{/if}
					</div>

					<!-- Right actions -->
					<div class="flex items-center gap-3">
						<button type="button" on:click={openLoginModal} class="rounded-lg px-3 py-1.5 text-sm font-medium text-[#666] transition-colors hover:bg-[#F5F5F5] hover:text-[#1A1A2E]">
							Sign in
						</button>
						<button type="button" on:click={openLoginModal} class="rounded-lg bg-[#6366F1] px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-[#4F46E5]">
							Get Started
						</button>
					</div>
				</header>

				<!-- Center -->
				<div class="flex flex-1 flex-col overflow-hidden">
					<div class="flex flex-1 flex-col items-center justify-center px-6 pt-8">
						<div class="flex w-full max-w-2xl flex-col items-center">
							<!-- Logo -->
							<div class="flex items-center justify-center">
								<span class="text-2xl font-bold">
									<span style="color:#1E1B4B;">hub</span><span style="color:#6366F1;">gate</span>
								</span>
							</div>

							<!-- Headline -->
							<h1 class="mt-6 text-center text-3xl font-bold tracking-tight sm:text-4xl" style="color:#1A1A2E;">
								Your Organisation's AI. <span style="color:#6366F1;">Governed.</span>
							</h1>
							<p class="mt-3 text-center text-base text-[#666] sm:text-lg">
								Ask anything. Secure, logged, grounded in your knowledge.
							</p>

							<!-- Chat Input -->
							<div class="mt-8 w-full">
								<div class="overflow-hidden rounded-2xl border border-[#E5E5E5] bg-white shadow-lg" style="box-shadow:0 4px 24px rgba(0,0,0,0.06);">
									<div class="px-5 py-4">
										<input
											type="text"
											bind:value={inputValue}
											on:keydown={(e) => e.key === 'Enter' && handleChatSubmit()}
											placeholder="Ask anything, or let us build your workspace..."
											class="w-full bg-transparent text-base text-[#1A1A2E] outline-none placeholder:text-[#999]"
										/>
									</div>
									<div class="flex items-center justify-between border-t border-[#F0F0F0] bg-[#FAFAFA] px-4 py-2.5">
										<div class="flex items-center gap-1">
											<button type="button" on:click={openLoginModal} class="flex h-8 w-8 items-center justify-center rounded-lg text-[#999] transition-colors hover:bg-white hover:text-[#666]">
												<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
													<path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
												</svg>
											</button>
											<button type="button" on:click={openLoginModal} class="flex h-8 w-8 items-center justify-center rounded-lg text-[#999] transition-colors hover:bg-white hover:text-[#666]">
												<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
													<path stroke-linecap="round" stroke-linejoin="round" d="M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9" />
												</svg>
											</button>
										</div>
										<div class="flex items-center gap-1.5">
											<button type="button" on:click={openLoginModal} class="flex h-8 w-8 items-center justify-center rounded-lg text-[#999] transition-colors hover:bg-white hover:text-[#666]">
												<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
													<path stroke-linecap="round" stroke-linejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
												</svg>
											</button>
											<button type="button" on:click={handleChatSubmit} class="flex h-9 w-9 items-center justify-center rounded-full bg-[#6366F1] text-white transition-colors hover:bg-[#4F46E5]">
												<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
													<path stroke-linecap="round" stroke-linejoin="round" d="M4.5 10.5L12 3m0 0l7.5 7.5M12 3v18" />
												</svg>
											</button>
										</div>
									</div>
								</div>
							</div>

							<!-- Suggested Prompts -->
							<div class="relative mt-4 w-full">
								<div class="flex flex-wrap justify-center gap-2">
									<button
										type="button"
										on:click={() => { inputValue = "I'd like to set up my AI workspace. Can you ask me a few questions about my role and daily tasks?"; openLoginModal(); }}
										class="flex items-center justify-center gap-1.5 rounded-full px-5 py-2.5 text-[13px] font-medium text-[#6366F1] transition-all hover:bg-[#F5F3FF]"
										style="border:1.5px solid #6366F1;"
									>
										<span>🚀</span>
										<span class="whitespace-nowrap">Help me get started</span>
									</button>
									{#each suggestedPrompts as prompt}
										<button
											type="button"
											on:click={openLoginModal}
											class="group relative flex min-w-[130px] items-center justify-center gap-1.5 rounded-full border border-[#E5E5E5] bg-white px-4 py-2.5 text-[13px] text-[#666] transition-all hover:border-[#6366F1]/30 hover:bg-[#6366F1]/5 hover:text-[#1A1A2E]"
										>
											<span class="whitespace-nowrap">{prompt.label}</span>
											<div class="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 -translate-x-1/2 whitespace-nowrap rounded-lg bg-white px-3 py-2 text-xs text-[#666] opacity-0 shadow-md transition-opacity duration-150 group-hover:opacity-100">
												{prompt.tooltip}
												<div class="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-white"></div>
											</div>
										</button>
									{/each}
								</div>
							</div>
						</div>
					</div>

					<!-- Feature Carousel -->
					{#if isCarouselVisible}
						<div
							class="flex-shrink-0 bg-[#F8F9FA] px-6 py-5"
							on:mouseenter={() => carouselPaused = true}
							on:mouseleave={() => carouselPaused = false}
						>
							<div class="relative mx-auto max-w-[680px]">
								<div class="relative flex h-[180px] overflow-hidden rounded-2xl border border-[#E5E5E5] bg-white shadow-md">
									<!-- Close -->
									<button
										type="button"
										on:click={() => isCarouselVisible = false}
										class="absolute right-3 top-3 z-10 flex h-5 w-5 items-center justify-center rounded-full text-[#999] transition-colors hover:text-[#1A1A2E]"
										aria-label="Close"
									>
										<svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
											<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
										</svg>
									</button>

									<!-- Illustration -->
									<div
										class="flex w-[272px] flex-shrink-0 items-center justify-center p-3 transition-all duration-[400ms]"
										style="background-color:{carouselSlides[carouselIndex].color}18;"
									>
										<div class="flex h-[156px] w-full items-center justify-center">
											{#if carouselSlides[carouselIndex].icon === 'shield'}
												<div class="flex h-full w-full flex-col items-center justify-center rounded-lg bg-white p-4 shadow-sm">
													<div class="flex h-20 w-20 items-center justify-center rounded-full bg-green-100">
														<svg xmlns="http://www.w3.org/2000/svg" class="h-12 w-12 text-green-600" viewBox="0 0 24 24" fill="currentColor">
															<path fill-rule="evenodd" d="M12.516 2.17a.75.75 0 00-1.032 0 11.209 11.209 0 01-7.877 3.08.75.75 0 00-.722.515A12.74 12.74 0 002.25 9.75c0 5.942 4.064 10.933 9.563 12.348a.749.749 0 00.374 0c5.499-1.415 9.563-6.406 9.563-12.348 0-1.399-.202-2.76-.578-4.035a.75.75 0 00-.722-.515 11.209 11.209 0 01-7.877-3.08z" clip-rule="evenodd" />
														</svg>
													</div>
													<div class="mt-3 flex items-center gap-1.5">
														<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-green-600" viewBox="0 0 24 24" fill="currentColor">
															<path fill-rule="evenodd" d="M12 1.5a5.25 5.25 0 00-5.25 5.25v3a3 3 0 00-3 3v6.75a3 3 0 003 3h10.5a3 3 0 003-3v-6.75a3 3 0 00-3-3v-3c0-2.9-2.35-5.25-5.25-5.25zm3.75 8.25v-3a3.75 3.75 0 10-7.5 0v3h7.5z" clip-rule="evenodd" />
														</svg>
														<span class="text-xs font-medium text-green-700">Enterprise-grade security</span>
													</div>
												</div>
											{:else if carouselSlides[carouselIndex].icon === 'files'}
												<div class="flex h-full w-full flex-col rounded-lg bg-white p-3 shadow-sm">
													<div class="mb-2.5 flex items-center justify-between">
														<span class="text-[11px] font-semibold text-gray-700">Documents</span>
														<span class="rounded bg-green-100 px-1.5 py-0.5 text-[8px] font-medium text-green-700">15 files</span>
													</div>
													<div class="flex flex-1 flex-col justify-center space-y-2">
														<div class="flex items-center gap-2 rounded-md bg-green-50 px-2.5 py-2">
															<span class="h-2 w-2 flex-shrink-0 rounded-full bg-green-500"></span>
															<span class="text-[10px] font-medium text-gray-700">FedRAMP Report.pdf</span>
														</div>
														<div class="flex items-center gap-2 rounded-md px-2.5 py-2">
															<span class="text-[10px] text-gray-600">Risk Policy.pdf</span>
														</div>
														<div class="flex items-center gap-2 rounded-md px-2.5 py-2">
															<span class="text-[10px] text-gray-600">Budget.xlsx</span>
														</div>
														<div class="flex items-center gap-2 rounded-md px-2.5 py-2">
															<span class="text-[10px] text-gray-600">Confluence</span>
														</div>
													</div>
												</div>
											{:else if carouselSlides[carouselIndex].icon === 'robot'}
												<div class="w-full rounded-lg bg-white p-2.5 shadow-sm">
													<div class="mb-2 text-[10px] font-semibold text-gray-700">Agents</div>
													<div class="space-y-1.5">
														<div class="flex items-center gap-1.5 rounded-lg bg-purple-50 px-2 py-1.5">
															<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-green-500"></span>
															<div class="flex h-5 w-5 items-center justify-center rounded-full bg-purple-500">
																<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 text-white" viewBox="0 0 24 24" fill="currentColor"><path d="M16.5 6.75a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0zM18 19.5a3 3 0 00-3-3H9a3 3 0 00-3 3v.5a.5.5 0 00.5.5h11a.5.5 0 00.5-.5v-.5z"/></svg>
															</div>
															<span class="flex-1 text-[9px] font-semibold text-purple-700">QA Reviewer</span>
															<span class="rounded bg-purple-100 px-1 py-0.5 text-[7px] text-purple-600">12</span>
														</div>
														<div class="flex items-center gap-1.5 rounded-lg border border-gray-200 px-2 py-1.5">
															<div class="flex h-5 w-5 items-center justify-center rounded-full bg-blue-500">
																<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 text-white" viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" d="M7.502 6h7.128A3.375 3.375 0 0118 9.375v9.375a3 3 0 003-3V6.108c0-1.505-1.125-2.811-2.664-2.94a48.972 48.972 0 00-.673-.05A3 3 0 0015 1.5h-1.5a3 3 0 00-2.663 1.618c-.225.015-.45.032-.673.05C8.662 3.295 7.554 4.542 7.502 6zM13.5 3A1.5 1.5 0 0012 4.5h4.5A1.5 1.5 0 0015 3h-1.5z" clip-rule="evenodd"/></svg>
															</div>
															<span class="flex-1 text-[9px] text-gray-600">Sales Drafter</span>
															<span class="rounded bg-gray-100 px-1 py-0.5 text-[7px] text-gray-500">8</span>
														</div>
														<div class="flex items-center gap-1.5 rounded-lg border border-gray-200 px-2 py-1.5">
															<div class="flex h-5 w-5 items-center justify-center rounded-full bg-amber-500">
																<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 text-white" viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" d="M5.625 1.5c-1.036 0-1.875.84-1.875 1.875v17.25c0 1.035.84 1.875 1.875 1.875h12.75c1.035 0 1.875-.84 1.875-1.875V12.75A3.75 3.75 0 0016.5 9h-1.875a1.875 1.875 0 01-1.875-1.875V5.25A3.75 3.75 0 009 1.5H5.625z" clip-rule="evenodd"/></svg>
															</div>
															<span class="flex-1 text-[9px] text-gray-600">Legal Counsel</span>
															<span class="rounded bg-gray-100 px-1 py-0.5 text-[7px] text-gray-500">5</span>
														</div>
													</div>
												</div>
											{:else if carouselSlides[carouselIndex].icon === 'clock'}
												<div class="w-full rounded-lg bg-white p-2.5 shadow-sm">
													<div class="mb-2 flex items-center justify-between">
														<span class="text-[10px] font-semibold text-gray-700">Activity Log</span>
														<span class="flex items-center gap-1 rounded bg-green-100 px-1.5 py-0.5 text-[7px] font-medium text-green-700">
															<span class="h-1.5 w-1.5 rounded-full bg-green-500"></span>
															Live
														</span>
													</div>
													<div class="space-y-1">
														{#each [['sarah@co.io','Claude','2m'],['alex@co.io','Gemini','5m'],['mike@co.io','GPT-4o','8m'],['lisa@co.io','Perplexity','12m']] as row}
															<div class="flex items-center gap-1.5 rounded bg-gray-50 px-2 py-1 text-[8px]">
																<span class="w-16 truncate text-gray-600">{row[0]}</span>
																<span class="w-10 font-medium text-gray-800">{row[1]}</span>
																<span class="w-6 text-gray-400">{row[2]}</span>
																<span class="ml-auto font-bold text-green-500">✓</span>
															</div>
														{/each}
													</div>
												</div>
											{:else if carouselSlides[carouselIndex].icon === 'sparkle'}
												<div class="grid w-full grid-cols-2 gap-1.5">
													{#each [{name:'Claude',logo:'/static/claude-logo.svg',speed:85,active:true},{name:'Gemini',logo:'/static/gemini-logo.png',speed:70,active:false},{name:'GPT',logo:'/static/openai-logo.svg',speed:75,active:false},{name:'Perplexity',logo:'/static/perplexity-logo.png',speed:90,active:false}] as m}
														<div class="relative flex flex-col items-center rounded-lg bg-white p-2 shadow-sm" style={m.active ? 'outline:2px solid rgba(99,102,241,0.4);' : ''}>
															{#if m.active}
																<span class="absolute -right-1 -top-1 flex items-center gap-0.5 rounded-full bg-green-500 px-1 py-0.5 text-[6px] font-medium text-white">
																	<span class="h-1 w-1 rounded-full bg-white"></span>Live
																</span>
															{/if}
															<img src={m.logo} alt={m.name} class="h-6 w-6 object-contain" />
															<span class="mt-1 text-[8px] font-medium text-gray-600">{m.name}</span>
															<div class="mt-1 h-1 w-full rounded-full bg-gray-100">
																<div class="h-full rounded-full bg-gradient-to-r from-blue-400 to-blue-500" style="width:{m.speed}%;"></div>
															</div>
														</div>
													{/each}
												</div>
											{:else}
												<!-- Confluence -->
												<div class="w-full rounded-lg bg-white p-2.5 shadow-sm">
													<div class="mb-2 flex items-center justify-between">
														<span class="text-[10px] font-semibold text-gray-700">Product Roadmap Q2</span>
														<span class="rounded bg-green-100 px-1.5 py-0.5 text-[7px] font-medium text-green-700">Synced</span>
													</div>
													<div class="space-y-1 rounded-md bg-blue-50 p-2">
														{#each ['Feature launch timeline','Resource allocation','Key milestones','Dependencies'] as item}
															<div class="flex items-center gap-1.5">
																<span class="text-[9px] text-blue-400">•</span>
																<span class="text-[8px] text-gray-600">{item}</span>
															</div>
														{/each}
													</div>
												</div>
											{/if}
										</div>
									</div>

									<!-- Content -->
									<div class="flex w-[60%] flex-col justify-center p-5">
										<h3 class="text-base font-bold text-[#1A1A2E]">{carouselSlides[carouselIndex].title}</h3>
										<p class="mt-1.5 text-sm leading-relaxed text-[#666]">{carouselSlides[carouselIndex].description}</p>
										<button
											type="button"
											on:click={openLoginModal}
											class="mt-3 inline-flex w-fit items-center rounded-lg bg-[#1E1B4B] px-3.5 py-1.5 text-sm font-medium text-white transition-colors hover:bg-[#312E81]"
										>
											{carouselSlides[carouselIndex].cta} →
										</button>
									</div>
								</div>

								<!-- Dots -->
								<div class="mt-4 flex justify-center gap-2">
									{#each carouselSlides as _, index}
										<button
											type="button"
											on:click={() => carouselIndex = index}
											class="h-2 rounded-full transition-all"
											style={index === carouselIndex ? 'width:24px;background:#1E1B4B;' : 'width:8px;background:#D9D9D9;'}
											aria-label="Go to slide {index + 1}"
										></button>
									{/each}
								</div>
							</div>
						</div>
					{/if}
				</div>

				<!-- Footer -->
				<footer class="flex h-14 flex-shrink-0 items-center justify-between border-t border-[#E5E5E5] bg-white px-6">
					<div class="flex items-center gap-2">
						<span class="text-sm font-bold">
							<span style="color:#1E1B4B;">hub</span><span style="color:#6366F1;">gate</span>
						</span>
						<span class="text-xs text-[#888]">© 2026 Hubgate. All rights reserved.</span>
					</div>
					<div class="flex items-center gap-4">
						<a href="#" class="text-xs text-[#888] hover:text-[#1A1A2E]">Privacy Policy</a>
						<span class="text-[#E5E5E5]">·</span>
						<a href="#" class="text-xs text-[#888] hover:text-[#1A1A2E]">Terms</a>
						<span class="text-[#E5E5E5]">·</span>
						<a href="#" class="text-xs text-[#888] hover:text-[#1A1A2E]">Security</a>
					</div>
					<div class="text-xs text-[#888]">Built on Open WebUI · Hosted in EU</div>
				</footer>
			</main>

			<!-- Floating Chat Widget -->
			<div class="fixed bottom-[72px] right-6 z-50">
				{#if isChatWidgetOpen}
					<div class="mb-4 w-80 overflow-hidden rounded-2xl bg-white shadow-xl" style="height:420px;">
						<div class="flex items-center justify-between bg-[#1E1B4B] px-4 py-3">
							<span class="font-medium text-white">Hubgate FAQ</span>
							<button type="button" on:click={() => isChatWidgetOpen = false} class="flex h-6 w-6 items-center justify-center rounded-full text-white/60 transition-colors hover:bg-white/10 hover:text-white">
								<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
									<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
								</svg>
							</button>
						</div>
						<div class="flex flex-col p-4" style="height:calc(100% - 52px);">
							<p class="mb-4 text-sm text-[#666]">Hi! How can I help you today? Click a question below or type your own.</p>
							<div class="flex flex-col gap-2">
								{#each ['What models are available?', 'Is my data secure?', 'How does pricing work?'] as q}
									<button type="button" class="rounded-lg border border-[#E5E5E5] bg-[#FAFAFA] px-3 py-2 text-left text-sm text-[#666] transition-colors hover:border-[#6366F1]/30 hover:bg-[#6366F1]/5">
										{q}
									</button>
								{/each}
							</div>
							<div class="mt-auto pt-4">
								<div class="flex items-center gap-2 rounded-lg border border-[#E5E5E5] bg-white px-3 py-2">
									<input type="text" placeholder="Type a question..." class="flex-1 bg-transparent text-sm outline-none placeholder:text-[#999]" />
									<button type="button" class="flex h-7 w-7 items-center justify-center rounded-full bg-[#6366F1] text-white">
										<svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
											<path stroke-linecap="round" stroke-linejoin="round" d="M4.5 10.5L12 3m0 0l7.5 7.5M12 3v18" />
										</svg>
									</button>
								</div>
							</div>
						</div>
					</div>
				{/if}
				<button
					type="button"
					on:click={() => isChatWidgetOpen = !isChatWidgetOpen}
					class="flex h-[52px] w-[52px] items-center justify-center rounded-full bg-[#6366F1] text-white shadow-lg transition-transform hover:scale-105"
				>
					<svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" viewBox="0 0 24 24" fill="currentColor">
						<path fill-rule="evenodd" d="M4.848 2.771A49.144 49.144 0 0112 2.25c2.43 0 4.817.178 7.152.52 1.978.292 3.348 2.024 3.348 3.97v6.02c0 1.946-1.37 3.678-3.348 3.97a48.901 48.901 0 01-3.476.383.39.39 0 00-.297.17l-2.755 4.133a.75.75 0 01-1.248 0l-2.755-4.133a.39.39 0 00-.297-.17 48.9 48.9 0 01-3.476-.384c-1.978-.29-3.348-2.024-3.348-3.97V6.741c0-1.946 1.37-3.68 3.348-3.97z" clip-rule="evenodd" />
					</svg>
				</button>
			</div>

			<!-- Login Modal -->
			{#if isLoginModalOpen}
				<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
				<div
					class="fixed inset-0 z-50 flex items-center justify-center"
					style="background:rgba(0,0,0,0.4);"
					on:click={() => isLoginModalOpen = false}
				>
					<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
					<div
						class="relative w-full max-w-[420px] rounded-2xl bg-white p-10 shadow-2xl"
						on:click|stopPropagation
					>
						<!-- Close -->
						<button
							type="button"
							on:click={() => isLoginModalOpen = false}
							class="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-full text-[#999] transition-colors hover:bg-[#F5F5F5] hover:text-[#1A1A2E]"
						>
							<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
								<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
							</svg>
						</button>

						<!-- Logo -->
						<div class="flex justify-center">
							<img src="/static/logo.png" alt="logo" class="h-10 rounded-full" crossorigin="anonymous" />
						</div>

						<!-- Headline -->
						<h2 class="mt-6 text-center text-xl font-bold text-[#1A1A2E]">
							{mode === 'signin' ? 'Sign in to continue' : ($config?.onboarding ? 'Create Admin Account' : 'Create your account')}
						</h2>
						<p class="mt-2 text-center text-sm text-[#888]">
							Your first €2 in AI usage is on us.
						</p>

						<!-- OAuth buttons -->
						{#if Object.keys($config?.oauth?.providers ?? {}).length > 0}
							<div class="mt-6 space-y-3">
								{#if $config?.oauth?.providers?.google}
									<button
										type="button"
										on:click={() => { window.location.href = `${WEBUI_BASE_URL}/oauth/google/login`; }}
										class="flex w-full items-center justify-center gap-3 rounded-lg border border-[#E5E5E5] bg-white px-4 py-3 text-sm font-medium text-[#1A1A2E] transition-colors hover:bg-[#F5F5F5]"
									>
										<svg class="h-5 w-5" viewBox="0 0 24 24">
											<path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
											<path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
											<path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
											<path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
										</svg>
										{$i18n.t('Continue with {{provider}}', { provider: 'Google' })}
									</button>
								{/if}
								{#if $config?.oauth?.providers?.microsoft}
									<button
										type="button"
										on:click={() => { window.location.href = `${WEBUI_BASE_URL}/oauth/microsoft/login`; }}
										class="flex w-full items-center justify-center gap-3 rounded-lg border border-[#E5E5E5] bg-white px-4 py-3 text-sm font-medium text-[#1A1A2E] transition-colors hover:bg-[#F5F5F5]"
									>
										<svg class="h-5 w-5" viewBox="0 0 23 23">
											<path fill="#f35325" d="M1 1h10v10H1z"/>
											<path fill="#81bc06" d="M12 1h10v10H12z"/>
											<path fill="#05a6f0" d="M1 12h10v10H1z"/>
											<path fill="#ffba08" d="M12 12h10v10H12z"/>
										</svg>
										{$i18n.t('Continue with {{provider}}', { provider: 'Microsoft' })}
									</button>
								{/if}
								{#if $config?.oauth?.providers?.github}
									<button
										type="button"
										on:click={() => { window.location.href = `${WEBUI_BASE_URL}/oauth/github/login`; }}
										class="flex w-full items-center justify-center gap-3 rounded-lg border border-[#E5E5E5] bg-white px-4 py-3 text-sm font-medium text-[#1A1A2E] transition-colors hover:bg-[#F5F5F5]"
									>
										<svg class="h-5 w-5" viewBox="0 0 24 24">
											<path fill="currentColor" d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.92 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57C20.565 21.795 24 17.31 24 12c0-6.63-5.37-12-12-12z"/>
										</svg>
										{$i18n.t('Continue with {{provider}}', { provider: 'GitHub' })}
									</button>
								{/if}
								{#if $config?.oauth?.providers?.oidc}
									<button
										type="button"
										on:click={() => { window.location.href = `${WEBUI_BASE_URL}/oauth/oidc/login`; }}
										class="flex w-full items-center justify-center gap-3 rounded-lg border border-[#E5E5E5] bg-white px-4 py-3 text-sm font-medium text-[#1A1A2E] transition-colors hover:bg-[#F5F5F5]"
									>
										<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
											<path stroke-linecap="round" stroke-linejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z" />
										</svg>
										{$i18n.t('Continue with {{provider}}', { provider: $config?.oauth?.providers?.oidc ?? 'SSO' })}
									</button>
								{/if}
							</div>
						{/if}

						<!-- Divider -->
						{#if Object.keys($config?.oauth?.providers ?? {}).length > 0 && ($config?.features.enable_login_form || $config?.features.enable_ldap || form)}
							<div class="my-6 flex items-center gap-4">
								<div class="h-px flex-1 bg-[#E5E5E5]"></div>
								<span class="text-sm text-[#999]">or</span>
								<div class="h-px flex-1 bg-[#E5E5E5]"></div>
							</div>
						{:else}
							<div class="mt-6"></div>
						{/if}

						<!-- Login Form -->
						{#if $config?.features.enable_login_form || $config?.features.enable_ldap || form}
							<form on:submit|preventDefault={submitHandler} class="space-y-3">
								{#if mode === 'signup'}
									<input
										bind:value={name}
										type="text"
										placeholder={$i18n.t('Full name')}
										autocomplete="name"
										required
										class="w-full rounded-lg border border-[#E5E5E5] bg-white px-4 py-3 text-sm text-[#1A1A2E] outline-none transition-colors placeholder:text-[#999] focus:border-[#6366F1] focus:ring-1 focus:ring-[#6366F1]"
									/>
								{/if}

								{#if mode === 'ldap'}
									<input
										bind:value={ldapUsername}
										type="text"
										placeholder={$i18n.t('Username')}
										autocomplete="username"
										required
										class="w-full rounded-lg border border-[#E5E5E5] bg-white px-4 py-3 text-sm text-[#1A1A2E] outline-none transition-colors placeholder:text-[#999] focus:border-[#6366F1] focus:ring-1 focus:ring-[#6366F1]"
									/>
								{:else}
									<input
										bind:value={email}
										type="email"
										placeholder={$i18n.t('Work email')}
										autocomplete="email"
										required
										class="w-full rounded-lg border border-[#E5E5E5] bg-white px-4 py-3 text-sm text-[#1A1A2E] outline-none transition-colors placeholder:text-[#999] focus:border-[#6366F1] focus:ring-1 focus:ring-[#6366F1]"
									/>
								{/if}

								<SensitiveInput
									bind:value={password}
									placeholder={$i18n.t('Password')}
									autocomplete={mode === 'signup' ? 'new-password' : 'current-password'}
									required
									outerClassName="flex w-full items-center rounded-lg border border-[#E5E5E5] bg-white px-4 py-3"
									inputClassName="flex-1 text-sm text-[#1A1A2E] bg-transparent outline-none placeholder:text-[#999]"
									showButtonClassName="pl-2 text-[#999] transition-colors hover:text-[#666]"
								/>

								{#if mode === 'signup' && $config?.features?.enable_signup_password_confirmation}
									<SensitiveInput
										bind:value={confirmPassword}
										placeholder={$i18n.t('Confirm Password')}
										autocomplete="new-password"
										required
										outerClassName="flex w-full items-center rounded-lg border border-[#E5E5E5] bg-white px-4 py-3"
										inputClassName="flex-1 text-sm text-[#1A1A2E] bg-transparent outline-none placeholder:text-[#999]"
										showButtonClassName="pl-2 text-[#999] transition-colors hover:text-[#666]"
									/>
								{/if}

								<button
									type="submit"
									class="flex w-full items-center justify-center gap-2 rounded-lg bg-[#6366F1] px-4 py-3 text-sm font-medium text-white transition-colors hover:bg-[#4F46E5]"
								>
									{#if mode === 'ldap'}
										{$i18n.t('Authenticate')}
									{:else if mode === 'signin'}
										{$i18n.t('Sign in')}
									{:else}
										{$config?.onboarding ? $i18n.t('Create Admin Account') : $i18n.t('Create Account')}
									{/if}
									<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
										<path stroke-linecap="round" stroke-linejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
									</svg>
								</button>
							</form>

							{#if $config?.features.enable_signup && !($config?.onboarding ?? false) && mode !== 'ldap'}
								<p class="mt-4 text-center text-sm text-[#888]">
									{mode === 'signin' ? $i18n.t("Don't have an account?") : $i18n.t('Already have an account?')}
									<button
										type="button"
										class="ml-1 font-medium text-[#6366F1] underline"
										on:click={() => mode = mode === 'signin' ? 'signup' : 'signin'}
									>
										{mode === 'signin' ? $i18n.t('Sign up') : $i18n.t('Sign in')}
									</button>
								</p>
							{/if}

							{#if $config?.features.enable_ldap && $config?.features.enable_login_form}
								<div class="mt-3 text-center">
									<button
										type="button"
										class="text-xs text-[#888] underline"
										on:click={() => { if (mode === 'ldap') mode = ($config?.onboarding ?? false) ? 'signup' : 'signin'; else mode = 'ldap'; }}
									>
										{mode === 'ldap' ? $i18n.t('Continue with Email') : $i18n.t('Continue with LDAP')}
									</button>
								</div>
							{/if}
						{/if}

						<p class="mt-4 text-center text-xs text-[#aaa]">
							No credit card until your €2 credit runs out.
						</p>

						{#if $config?.metadata?.login_footer}
							<div class="mt-4 text-center text-[0.7rem] text-[#999]">
								{@html DOMPurify.sanitize(marked($config?.metadata?.login_footer))}
							</div>
						{/if}
					</div>
				</div>
			{/if}
		</div>
	{/if}
{/if}
