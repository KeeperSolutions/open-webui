import { goto } from '$app/navigation';
import { getBackendConfig } from '$lib/apis';
import { updateUserTimezone } from '$lib/apis/auths';
import { config, user, socket } from '$lib/stores';
import { getUserTimezone } from '$lib/utils';
import { toast } from 'svelte-sonner';
import { get } from 'svelte/store';

export const handleAuthSuccess = async (sessionUser: Record<string, any>) => {
	toast.success("You're now logged in.");
	if (sessionUser.token) {
		localStorage.token = sessionUser.token;
	}
	get(socket)?.emit('user-join', { auth: { token: sessionUser.token } });
	await user.set(sessionUser);
	await config.set(await getBackendConfig());
	const timezone = getUserTimezone();
	if (sessionUser.token && timezone) {
		updateUserTimezone(sessionUser.token, timezone);
	}
	goto('/chat');
};
