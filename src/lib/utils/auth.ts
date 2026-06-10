import { goto } from '$app/navigation';
import { getBackendConfig } from '$lib/apis';
import { updateUserTimezone } from '$lib/apis/auths';
import { config, user, socket, type SessionUser } from '$lib/stores';
import { getUserTimezone } from '$lib/utils';
import { toast } from 'svelte-sonner';
import { get } from 'svelte/store';

const applyThemeFromLocalStorage = () => {
	const theme = localStorage.theme ?? 'system';
	const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
	const isDark = theme === 'dark' || theme === 'oled-dark' || (theme === 'system' && prefersDark);

	const html = document.documentElement;

	if (theme === 'oled-dark') {
		html.style.setProperty('--color-gray-800', '#101010');
		html.style.setProperty('--color-gray-850', '#050505');
		html.style.setProperty('--color-gray-900', '#000000');
		html.style.setProperty('--color-gray-950', '#000000');
	} else {
		html.style.removeProperty('--color-gray-800');
		html.style.removeProperty('--color-gray-850');
		html.style.removeProperty('--color-gray-900');
		html.style.removeProperty('--color-gray-950');
	}

	html.classList.remove('light', 'dark', 'her');
	html.classList.add(isDark ? 'dark' : theme === 'her' ? 'her' : 'light');

	const metaThemeColor = document.querySelector('meta[name="theme-color"]');
	if (metaThemeColor) {
		const color =
			theme === 'oled-dark' ? '#000000' :
			theme === 'her' ? '#983724' :
			isDark ? '#171717' : '#ffffff';
		metaThemeColor.setAttribute('content', color);
	}
};

export const handleAuthSuccess = async (sessionUser: SessionUser & { token?: string }) => {
	toast.success("You're now logged in.");
	if (sessionUser.token) {
		localStorage.token = sessionUser.token;
		applyThemeFromLocalStorage();
	}
	get(socket)?.emit('user-join', { auth: { token: sessionUser.token } });
	user.set(sessionUser);
	config.set(await getBackendConfig());
	const timezone = getUserTimezone();
	if (sessionUser.token && timezone) {
		updateUserTimezone(sessionUser.token, timezone);
	}
	const stored = localStorage.getItem('postLoginRedirect') ?? '';
	localStorage.removeItem('postLoginRedirect');
	const redirect = stored.startsWith('/') ? stored : '/chat';
	goto(redirect);
};
