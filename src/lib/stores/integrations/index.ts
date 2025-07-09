import { writable } from 'svelte/store';

export const apiSpec = writable<string | null>(null);

interface ConfidiosState {
	isAdminLoggedIn: boolean;
	// balance?: string;
	currentUserStatus?: {
		isConfidiosUser: boolean;
		username?: string;
		balance?: string;
	};
}

const initialState: ConfidiosState = {
	isAdminLoggedIn: false,
	currentUserStatus: undefined
};

export const confidiosStore = writable<ConfidiosState>(initialState);
