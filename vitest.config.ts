import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { resolve } from 'path';

export default defineConfig({
	plugins: [
		svelte({ hot: false })
	],
	resolve: {
		conditions: ['browser'],
		alias: {
			$lib: resolve('./src/lib'),
			$app: resolve('./node_modules/@sveltejs/kit/src/runtime/app')
		}
	},
	test: {
		environment: 'jsdom',
		setupFiles: ['src/setupTests.ts'],
		include: ['src/**/*.test.ts'],
		globals: true
	}
});
