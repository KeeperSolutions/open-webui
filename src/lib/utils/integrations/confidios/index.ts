import { WEBUI_API_BASE_URL } from '$lib/constants';
import { confidiosStore } from '$lib/stores/integrations';
import { user } from '$lib/stores';
import { get } from 'svelte/store';
import { toast } from 'svelte-sonner';

export async function handleConfidiosLogin() {
	let isConfidiosLoading = true;

	try {
		const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/auths/user/login`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${localStorage.token}`
			},
			body: JSON.stringify({
				confidios_username: get(user).email.replace('@', '-at-').toLowerCase()
			})
		});

		if (!response.ok) {
			throw new Error('Failed to login to Confidios');
		}

		const data = await response.json();
		console.log('Confidios login response:', data);

		toast.success(data.status || 'Successfully logged in to Confidios');

		confidiosStore.update((state) => ({
			...state,
			currentUserStatus: {
				isConfidiosUser: true,
				isLoggedInConfidios: true,
				username: data.confidios_user,
				balance: data.confidios_balance
			}
		}));

		return true;
	} catch (error) {
		console.error('Error logging into Confidios:', error);
		toast.error('Failed to login to Confidios. Please try again.');

		confidiosStore.update((state) => ({
			...state,
			currentUserStatus: {
				...state.currentUserStatus,
				isLoggedInConfidios: false
			}
		}));

		return false;
	} finally {
		isConfidiosLoading = false;
	}
}

export async function handleConfidiosLogout() {
	try {
		const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/auths/user/logout`, {
			method: 'POST',
			headers: {
				Authorization: `Bearer ${localStorage.token}`
			}
		});

		if (!response.ok) {
			throw new Error('Failed to logout from Confidios');
		}

		const data = await response.json();
		console.log('Confidios logout response:', data);

		toast.success(data.status || 'Successfully logged out from Confidios');

		confidiosStore.update((state) => ({
			...state,
			currentUserStatus: {
				...state.currentUserStatus,
				isLoggedInConfidios: false,
				balance: undefined
			}
		}));

		return true;
	} catch (error) {
		console.error('Error logging out from Confidios:', error);
		toast.error('Failed to logout from Confidios. Please try again.');
		return false;
	}
}
