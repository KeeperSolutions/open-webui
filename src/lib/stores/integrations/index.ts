import { writable } from 'svelte/store';

export const apiSpec = writable<string | null>(null);
