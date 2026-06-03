import typography from '@tailwindcss/typography';
import containerQueries from '@tailwindcss/container-queries';

/** @type {import('tailwindcss').Config} */
export default {
	darkMode: 'class',
	content: ['./src/**/*.{html,js,svelte,ts}'],
	theme: {
		extend: {
			colors: {
				'hg-blue': {
					DEFAULT: '#2563eb',
					hover: '#1d4ed8'
				},
				'hg-orange': {
					DEFAULT: '#f97316'
				},
				'hg-text': {
					primary: '#1c1917',
					secondary: '#57534e',
					tertiary: '#a8a29e',
					emphasis: '#292524'
				},
				'hg-border': {
					DEFAULT: '#e7e5e4',
					focus: '#2563eb',
					subtle: '#f5f5f4'
				},
				'hg-bg': {
					surface: '#ffffff',
					muted: '#f5f5f4'
				},
				'hg-error': {
					text: '#991b1b',
					bg: '#fef2f2'
				},
				'hg-success': {
					400: '#34d399',
					600: '#16a34a'
				},
				gray: {
					50: 'var(--color-gray-50, #f9f9f9)',
					100: 'var(--color-gray-100, #ececec)',
					200: 'var(--color-gray-200, #e3e3e3)',
					300: 'var(--color-gray-300, #cdcdcd)',
					400: 'var(--color-gray-400, #b4b4b4)',
					500: 'var(--color-gray-500, #9b9b9b)',
					600: 'var(--color-gray-600, #676767)',
					700: 'var(--color-gray-700, #4e4e4e)',
					800: 'var(--color-gray-800, #333)',
					850: 'var(--color-gray-850, #262626)',
					900: 'var(--color-gray-900, #171717)',
					950: 'var(--color-gray-950, #0d0d0d)'
				}
			},
			typography: {
				DEFAULT: {
					css: {
						pre: false,
						code: false,
						'pre code': false,
						'code::before': false,
						'code::after': false
					}
				}
			},
			fontFamily: {
				'hg-heading': ['"Space Grotesk"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
				'hg-body': ['Manrope', 'ui-sans-serif', 'system-ui', 'sans-serif']
			},
			borderRadius: {
				'hg-sm': '4px',
				'hg-md': '8px',
				'hg-full': '9999px'
			},
			padding: {
				'safe-bottom': 'env(safe-area-inset-bottom)'
			},
			transitionProperty: {
				width: 'width'
			}
		}
	},
	plugins: [typography, containerQueries]
};
