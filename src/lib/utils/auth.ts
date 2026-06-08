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
	html.classList.remove('light', 'dark', 'her');
	html.classList.add(isDark ? 'dark' : theme === 'her' ? 'her' : 'light');
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
	goto('/chat');
};
