// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';

import HgFeatureCarousel from './HgFeatureCarousel.svelte';
import { features } from '$lib/data/landing-features';

// Illustration components are SVG-heavy — stub them out
vi.mock('$lib/data/landing-features', () => ({
	features: [
		{ title: 'Feature One', description: 'Desc one', illustration: null },
		{ title: 'Feature Two', description: 'Desc two', illustration: null },
		{ title: 'Feature Three', description: 'Desc three', illustration: null }
	]
}));

const renderCarousel = () => render(HgFeatureCarousel);

const activeTab = () =>
	screen.getAllByRole('tab').find((t) => t.getAttribute('aria-selected') === 'true')!;

const tabAt = (i: number) => screen.getAllByRole('tab')[i];

beforeEach(() => {
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
	vi.clearAllMocks();
});

// ─── dot navigation ───────────────────────────────────────────────────────────

describe('dot navigation', () => {
	it('starts on the first slide', () => {
		renderCarousel();
		expect(tabAt(0).getAttribute('aria-selected')).toBe('true');
		expect(screen.getByText('Feature One')).toBeInTheDocument();
	});

	it('clicking a dot makes it the active slide', async () => {
		renderCarousel();
		await fireEvent.click(tabAt(1));
		expect(tabAt(1).getAttribute('aria-selected')).toBe('true');
		expect(screen.getByText('Feature Two')).toBeInTheDocument();
	});

	it('clicking the last dot shows the last slide', async () => {
		renderCarousel();
		await fireEvent.click(tabAt(features.length - 1));
		expect(screen.getByText('Feature Three')).toBeInTheDocument();
	});

	it('renders one dot per feature', () => {
		renderCarousel();
		expect(screen.getAllByRole('tab')).toHaveLength(features.length);
	});
});

// ─── autoplay ─────────────────────────────────────────────────────────────────

describe('autoplay', () => {
	it('advances to the next slide after 4 seconds', async () => {
		renderCarousel();
		expect(screen.getByText('Feature One')).toBeInTheDocument();

		vi.advanceTimersByTime(4000);
		await Promise.resolve();

		expect(screen.getByText('Feature Two')).toBeInTheDocument();
	});

	it('wraps back to the first slide after the last', async () => {
		renderCarousel();
		vi.advanceTimersByTime(4000 * features.length);
		await Promise.resolve();

		expect(screen.getByText('Feature One')).toBeInTheDocument();
	});

	it('pauses on mouseenter and does not advance', async () => {
		renderCarousel();
		const region = screen.getByRole('region');

		await fireEvent.mouseEnter(region);
		vi.advanceTimersByTime(4000);
		await Promise.resolve();

		expect(screen.getByText('Feature One')).toBeInTheDocument();
	});

	it('resumes advancing after mouseleave', async () => {
		renderCarousel();
		const region = screen.getByRole('region');

		await fireEvent.mouseEnter(region);
		vi.advanceTimersByTime(4000);
		await Promise.resolve();
		expect(screen.getByText('Feature One')).toBeInTheDocument();

		await fireEvent.mouseLeave(region);
		vi.advanceTimersByTime(4000);
		await Promise.resolve();

		expect(screen.getByText('Feature Two')).toBeInTheDocument();
	});
});

// ─── swipe ────────────────────────────────────────────────────────────────────

describe('touch swipe', () => {
	const swipe = async (el: Element, dx: number, dy = 0) => {
		await fireEvent.touchStart(el, { touches: [{ clientX: 200, clientY: 200 }] });
		await fireEvent.touchEnd(el, {
			changedTouches: [{ clientX: 200 + dx, clientY: 200 + dy }]
		});
	};

	it('swipe left advances to the next slide', async () => {
		renderCarousel();
		await swipe(screen.getByRole('region'), -80);
		await Promise.resolve();
		expect(screen.getByText('Feature Two')).toBeInTheDocument();
	});

	it('swipe right goes to the previous slide (wraps to last)', async () => {
		renderCarousel();
		await swipe(screen.getByRole('region'), 80);
		await Promise.resolve();
		expect(screen.getByText('Feature Three')).toBeInTheDocument();
	});

	it('ignores a swipe shorter than 30px', async () => {
		renderCarousel();
		await swipe(screen.getByRole('region'), -20);
		await Promise.resolve();
		expect(screen.getByText('Feature One')).toBeInTheDocument();
	});

	it('ignores a swipe that is more vertical than horizontal', async () => {
		renderCarousel();
		await swipe(screen.getByRole('region'), -40, -80);
		await Promise.resolve();
		expect(screen.getByText('Feature One')).toBeInTheDocument();
	});
});
