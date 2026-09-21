// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';

import Spotlight from './Spotlight.svelte';

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.useRealTimers());

const makeTarget = (rect: Partial<DOMRect> = {}) => {
	const el = document.createElement('div');
	el.getBoundingClientRect = () =>
		({
			top: 100,
			left: 50,
			width: 200,
			height: 40,
			bottom: 140,
			right: 250,
			x: 50,
			y: 100,
			toJSON: () => ({}),
			...rect
		}) as DOMRect;
	document.body.appendChild(el);
	return el;
};

describe('Spotlight', () => {
	it('renders nothing when show is false', () => {
		const target = makeTarget();
		const { container } = render(Spotlight, {
			props: { show: false, target, message: 'Try this gesture' }
		});
		expect(container.querySelector('.fixed.inset-0')).toBeNull();
	});

	it('renders nothing when there is no target, even if show is true', () => {
		const { container } = render(Spotlight, {
			props: { show: true, target: null, message: 'Try this gesture' }
		});
		expect(container.querySelector('.fixed.inset-0')).toBeNull();
	});

	it('renders the message and title once shown with a target', () => {
		const target = makeTarget();
		const { getByText } = render(Spotlight, {
			props: { show: true, target, title: 'Long-press', message: 'Try this gesture' }
		});
		expect(getByText('Long-press')).toBeInTheDocument();
		expect(getByText('Try this gesture')).toBeInTheDocument();
	});

	it('renders inline markup in the message (e.g. bolding the gesture word)', () => {
		const target = makeTarget();
		const { container } = render(Spotlight, {
			props: { show: true, target, message: 'Touch and <strong>hold</strong> a chat.' }
		});
		expect(container.querySelector('strong')?.textContent).toBe('hold');
	});

	it('positions the ring over the target rect', () => {
		const target = makeTarget();
		const { container } = render(Spotlight, {
			props: { show: true, target, message: 'msg', padding: 8 }
		});
		const ring = container.querySelector('.pointer-events-none.ring-2') as HTMLElement;
		expect(ring.style.top).toBe('92px'); // 100 - padding
		expect(ring.style.left).toBe('42px'); // 50 - padding
		expect(ring.style.width).toBe('216px'); // 200 + padding*2
		expect(ring.style.height).toBe('56px'); // 40 + padding*2
	});

	it('shifts (rather than clips) the ring when the target is near the viewport edge, so its height stays intact', () => {
		// A target whose padded bottom edge would fall past window.innerHeight
		const target = makeTarget({
			top: window.innerHeight - 20,
			bottom: window.innerHeight - 5,
			height: 15
		});
		const { container } = render(Spotlight, {
			props: { show: true, target, message: 'msg', padding: 8 }
		});
		const ring = container.querySelector('.pointer-events-none.ring-2') as HTMLElement;

		// Unclamped height would be 15 + 8*2 = 31 - clipping would shrink this, shifting keeps it
		expect(parseFloat(ring.style.height)).toBe(31);
		// The bottom edge must not exceed the viewport (minus the small edge margin)
		expect(parseFloat(ring.style.top) + parseFloat(ring.style.height)).toBeLessThanOrEqual(window.innerHeight);
	});

	it('the backdrop covers the full viewport and is not dismissible by clicking it', async () => {
		const target = makeTarget();
		const onDismiss = vi.fn();
		const { container } = render(Spotlight, {
			props: { show: true, target, message: 'msg', onDismiss }
		});

		const wrapper = container.querySelector('.fixed.inset-0') as HTMLElement;
		expect(wrapper).not.toBeNull();

		// The cutout div darkens everything else via its own box-shadow, keeping its own area lit
		const cutout = container.querySelector('[style*="box-shadow"]') as HTMLElement;
		expect(cutout.getAttribute('style')).toMatch(/box-shadow:\s*0 0 0 9999px/);

		await fireEvent.click(wrapper);
		await fireEvent.click(cutout);

		expect(onDismiss).not.toHaveBeenCalled();
	});

	it('only the callout button dismisses', () => {
		const target = makeTarget();
		const { container } = render(Spotlight, {
			props: { show: true, target, message: 'msg', dismissLabel: 'Next' }
		});

		const callout = container.querySelector('.pointer-events-auto') as HTMLElement;
		expect(callout).not.toBeNull();
		expect(callout.querySelector('button')?.textContent?.trim()).toBe('Next');
	});

	it('closes (removes the overlay) when the dismiss button is clicked', async () => {
		const target = makeTarget();
		const onDismiss = vi.fn();
		const { getByText } = render(Spotlight, {
			props: { show: true, target, message: 'msg', dismissLabel: 'Got it', onDismiss }
		});

		await fireEvent.click(getByText('Got it'));

		expect(onDismiss).toHaveBeenCalledOnce();
	});

	it('auto-dismisses after the timeout with no interaction', async () => {
		const target = makeTarget();
		const onDismiss = vi.fn();
		render(Spotlight, {
			props: { show: true, target, message: 'msg', autoDismissMs: 50, onDismiss }
		});

		await new Promise((resolve) => setTimeout(resolve, 300));

		expect(onDismiss).toHaveBeenCalledOnce();
	});
});
