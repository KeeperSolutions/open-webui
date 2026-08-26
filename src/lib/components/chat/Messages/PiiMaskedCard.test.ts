// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import { readable } from 'svelte/store';

import PiiMaskedCard from './PiiMaskedCard.svelte';

// jsdom lacks the Web Animations API that bits-ui / Svelte transitions call via
// `element.animate`. Stub it so rendering never throws here.
if (!Element.prototype.animate) {
	Element.prototype.animate = function () {
		const anim = {
			onfinish: null as (() => void) | null,
			cancel() {},
			finished: Promise.resolve()
		};
		queueMicrotask(() => anim.onfinish?.());
		return anim as unknown as Animation;
	};
}

// i18n mock: return the key and interpolate vars — mirrors the repo's
// existing component-test convention (see HgAuthCard.test.ts).
const i18n = readable({
	t: (key: string, vars?: Record<string, string>) => {
		if (!vars) return key;
		return key.replace(/\{\{(\w+)\}\}/g, (_, k) => vars[k] ?? k);
	}
});

const renderCard = (props = {}) =>
	render(PiiMaskedCard, { props, context: new Map([['i18n', i18n]]) });

// "Zovem se Ivan Horvat" — char offsets: "Ivan Horvat" = [9, 20)
const SENTENCE = 'Zovem se Ivan Horvat';

describe('PiiMaskedCard', () => {
	it('renders nothing when there are no detections', () => {
		const { container } = renderCard({ detections: [], originalText: SENTENCE });
		expect(container.textContent).toBe('');
	});

	it('shows the collapsed badge with the masked count and keeps values hidden', () => {
		renderCard({
			detections: [{ type: 'PERSON', start: 9, end: 20 }],
			originalText: SENTENCE
		});
		// count is interpolated into the badge label
		expect(screen.getByText('1 values masked')).toBeTruthy();
		// value lives in the popover content and is not rendered while collapsed
		expect(screen.queryByText('Ivan Horvat')).toBeNull();
	});

	it('dedupes identical (type, value) occurrences in the count', () => {
		// "Ivan Ivan" — two PERSON spans resolving to the same value
		renderCard({
			detections: [
				{ type: 'PERSON', start: 0, end: 4 },
				{ type: 'PERSON', start: 5, end: 9 }
			],
			originalText: 'Ivan Ivan'
		});
		expect(screen.getByText('1 values masked')).toBeTruthy();
	});

	it('counts distinct entities separately', () => {
		// "Ivan Horvat and Ana Anic": [0,11) and [16,24)
		renderCard({
			detections: [
				{ type: 'PERSON', start: 0, end: 11 },
				{ type: 'PERSON', start: 16, end: 24 }
			],
			originalText: 'Ivan Horvat and Ana Anic'
		});
		expect(screen.getByText('2 values masked')).toBeTruthy();
	});

	// Regression (bits-ui 0.21 -> 2.x): Content used to be portalled to <body> by
	// default. In 2.x it renders in place unless wrapped in Popover.Portal, and the
	// chat column above it is a Tailwind `@container` (container-type: inline-size),
	// which becomes the containing block for position:fixed descendants -- the panel
	// then lands hundreds of px off the right edge of the viewport and reads as
	// "clicking the badge does nothing". Assert the content escapes the component.
	it('portals the opened panel out to the document body', async () => {
		const { container } = renderCard({
			detections: [{ type: 'PERSON', start: 9, end: 20 }],
			originalText: SENTENCE
		});
		await fireEvent.click(screen.getByText('1 values masked'));

		const content = document.querySelector('[data-popover-content]');
		expect(content).not.toBeNull();
		expect(screen.getByText('Ivan Horvat')).toBeTruthy();
		// escaped the component's own subtree
		expect(container.contains(content)).toBe(false);
		// the hook the open/close CSS animation keys off (see the component's
		// <style> block) — asserted so markup and selector can't drift apart
		expect(content?.classList.contains('pii-masked-panel')).toBe(true);
	});

	it('filters out detections whose offsets yield an empty value', () => {
		// offsets out of range for the given text -> empty slice -> filtered
		const { container } = renderCard({
			detections: [{ type: 'EMAIL', start: 100, end: 120 }],
			originalText: SENTENCE
		});
		expect(container.textContent).toBe('');
	});
});
