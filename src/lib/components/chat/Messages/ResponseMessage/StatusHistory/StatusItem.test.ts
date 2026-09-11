// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/svelte';
import { readable } from 'svelte/store';
import StatusItem from './StatusItem.svelte';

// i18n mock: returns the key with `{{var}}` placeholders filled in, the same
// mock used in PiiMaskedCard.test.ts.
const i18n = readable({
	t: (key: string, vars?: Record<string, string>) => {
		if (!vars) return key;
		return key.replace(/\{\{(\w+)\}\}/g, (_, k) => vars[k] ?? k);
	}
});

const status = (over: Record<string, unknown> = {}) => ({
	action: 'pii_masking',
	description: 'Masking sensitive data',
	count: 3,
	total: 10,
	done: false,
	...over
});

const renderStatus = (props = {}) =>
	render(StatusItem, { props, context: new Map([['i18n', i18n]]) });

describe('StatusItem — pii_masking', () => {
	it('shows how far the masking has got, not just that it is running', () => {
		const { container } = renderStatus({ status: status() });
		expect(container.textContent).toContain('3');
		expect(container.textContent).toContain('10');
	});

	it('shimmers while masking is unfinished', () => {
		const { container } = renderStatus({ status: status() });
		expect(container.querySelector('.shimmer')).not.toBeNull();
	});

	it('stops shimmering once every chunk is masked', () => {
		const { container } = renderStatus({
			status: status({ done: true, count: 10 }),
			done: true
		});
		expect(container.querySelector('.shimmer')).toBeNull();
	});
});
