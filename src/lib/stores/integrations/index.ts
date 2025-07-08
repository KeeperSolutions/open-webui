import { writable } from 'svelte/store';

export const apiSpec = writable<string | null>(null);

interface ConfidiosState {
	isAdminLoggedIn: boolean;
	balance?: string;
}

const initialState: ConfidiosState = {
	isAdminLoggedIn: false
};

export const confidiosStore = writable<ConfidiosState>(initialState);
