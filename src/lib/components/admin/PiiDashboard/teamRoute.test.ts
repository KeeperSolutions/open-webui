// @vitest-environment node
/**
 * The team dashboard route's own wrapper.
 *
 * ⚠️ Structural, and it has to be. A layout defect is invisible to every test in
 * this project: jsdom performs no layout, so a component that renders the right
 * markup at the wrong width passes everything. This one was found by looking at a
 * screenshot, three gates after it shipped, and the only reason it survived that
 * long is that the dashboard was always checked by reading the DOM rather than by
 * measuring it.
 *
 * What is pinned here is the smallest thing a test can hold: that the route
 * supplies a container which fills the row AND reserves the sidebar's width.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const TEAM_ROUTE = 'src/routes/(app)/team/[team_id]/pii-dashboard/+page.svelte';
const ADMIN_LAYOUT = 'src/routes/(app)/admin/+layout.svelte';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf-8');

describe('the team dashboard route brings its own container', () => {
	const source = read(TEAM_ROUTE);

	/** Every `class="..."` in the file, so a claim can be made about ONE element. */
	const classAttributes = [...source.matchAll(/class="([^"]*)"/g)].map((m) => m[1]);

	it('⚠️ has ONE element that both fills the row and reserves the sidebar', () => {
		/**
		 * `(app)/+layout.svelte` renders the sidebar and then a bare `<slot />`.
		 * Without `flex-1` the dashboard sizes itself to its content — capped at
		 * `max-w-[1190px]` — and the row's `justify-content: flex-end` pushes that
		 * block to the right edge, leaving the surplus as an empty band that grows
		 * with the window. Measured at 1200px: left edge 298 instead of 260.
		 *
		 * ⚠️ But `flex-1` alone is NOT the fix, and the first attempt proved it:
		 * `#sidebar` is out of flow, so a stretched item spans the whole viewport
		 * and slides UNDERNEATH it. Measured left edge 1, sidebar ending at 260 —
		 * worse than the defect it replaced.
		 *
		 * Both on the SAME element, because either alone is a different bug. Asked
		 * of the file as a whole this would pass with `flex-1` on some inner
		 * scroller and the clamp nowhere — which is exactly what the first version
		 * of this test did, and a mutation caught it.
		 */
		const wrapper = classAttributes.find(
			(c) => c.includes('flex-1') && c.includes('md:max-w-[calc(100%-var(--sidebar-width))]')
		);
		expect(wrapper, `no single element carries both:\n${classAttributes.join('\n')}`).toBeTruthy();
	});

	it('reserves the narrower rail when the sidebar is collapsed', () => {
		expect(source).toContain('md:max-w-[calc(100%-49px)]');
	});

	it('keeps the two clamps identical to the admin layout it is copied from', () => {
		/**
		 * ⚠️ Copied, not reinvented — including the 49px rail for the collapsed
		 * sidebar. If the shell ever changes how it reserves that width, this test
		 * fails on the route that has no layout to inherit the change from.
		 */
		const admin = read(ADMIN_LAYOUT);
		for (const clamp of [
			'md:max-w-[calc(100%-var(--sidebar-width))]',
			'md:max-w-[calc(100%-49px)]'
		]) {
			expect(admin).toContain(clamp);
		}
	});

	it('still keys the dashboard on the team id', () => {
		// Unchanged by the wrapper, and the reason it exists is unrelated: a param
		// change would otherwise reuse loaders bound to the previous team.
		expect(source).toContain('{#key teamId}');
	});
});
