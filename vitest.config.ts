import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { resolve } from 'path';

export default defineConfig({
	plugins: [
		svelte({ hot: false })
	],
	// Build-time globals injected by SvelteKit; defined here so modules that import
	// `$app/environment` (e.g. $lib/constants) resolve under vitest.
	define: {
		__SVELTEKIT_APP_VERSION__: JSON.stringify('test'),
		__SVELTEKIT_APP_VERSION_POLL_INTERVAL__: '0',
		__SVELTEKIT_DEV__: 'false',
		__SVELTEKIT_EMBEDDED__: 'false',
		__SVELTEKIT_EXPERIMENTAL_EXPLICIT_ENVIRONMENT_VARIABLES__: 'false',
		// App globals normally injected by vite.config.ts
		APP_VERSION: JSON.stringify(process.env.npm_package_version ?? 'test'),
		APP_BUILD_HASH: JSON.stringify('dev-build')
	},
	resolve: {
		conditions: ['browser'],
		alias: {
			$lib: resolve('./src/lib'),
			$app: resolve('./node_modules/@sveltejs/kit/src/runtime/app'),
			// SvelteKit virtual module — injected at build time, stubbed for vitest
			'$env/static/public': resolve('./src/test-stubs/env-static-public.ts')
		}
	},
	test: {
		environment: 'jsdom',
		setupFiles: ['src/setupTests.ts'],
		include: ['src/**/*.test.ts'],
		globals: true
	}
});
