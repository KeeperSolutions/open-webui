import { writable } from 'svelte/store';

export const apiSpec = writable<string | null>(null);

interface ConfidiosState {
	isAdminLoggedIn: boolean;
	currentUserStatus?: {
		isConfidiosUser: boolean; // Has Confidios account
		isLoggedInConfidios: boolean; // Currently logged in
		username?: string;
		balance?: string;
	};
}

const initialState: ConfidiosState = {
	isAdminLoggedIn: false,
	currentUserStatus: undefined
};

export const confidiosStore = writable<ConfidiosState>(initialState);
