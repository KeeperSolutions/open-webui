// @vitest-environment node
/**
 * The team dashboard route's own page container.
 *
 * Reads the source because jsdom does no layout. Pins that the route supplies a
 * container that fills the row and reserves the sidebar's width.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const TEAM_ROUTE = 'src/routes/(app)/team/[team_id]/pii-dashboard/+page.svelte';
const ADMIN_LAYOUT = 'src/routes/(app)/admin/+layout.svelte';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf-8');

describe('the team dashboard route brings its own container', () => {
	const source = read(TEAM_ROUTE);

	/** Every `class="..."` in the file, so a check can target a single element. */
	const classAttributes = [...source.matchAll(/class="([^"]*)"/g)].map((m) => m[1]);

	it('has one element that both fills the row and reserves the sidebar', () => {
		/**
		 * Both must be on the same element. Without `flex-1` the dashboard shrinks
		 * to its content and leaves an empty band beside the sidebar. With `flex-1`
		 * alone it slides under the out-of-flow `#sidebar`. A file-wide check would
		 * pass with `flex-1` on an inner scroller and no clamp at all.
		 */
		const wrapper = classAttributes.find(
			(c) => c.includes('flex-1') && c.includes('md:max-w-[calc(100%-var(--sidebar-width))]')
		);
		expect(wrapper, `no single element carries both:\n${classAttributes.join('\n')}`).toBeTruthy();
	});

	it('reserves the narrower rail when the sidebar is collapsed', () => {
		expect(source).toContain('md:max-w-[calc(100%-42px)]');
	});

	it('keeps the two clamps identical to the admin layout it is copied from', () => {
		// The clamps, including the 42px collapsed rail, are copied from the admin
		// layout. This fails if the admin layout changes and the route is not updated.
		const admin = read(ADMIN_LAYOUT);
		for (const clamp of [
			'md:max-w-[calc(100%-var(--sidebar-width))]',
			'md:max-w-[calc(100%-42px)]'
		]) {
			expect(admin).toContain(clamp);
		}
	});

	it('still keys the dashboard on the team id', () => {
		// A param change would otherwise reuse loaders bound to the previous team.
		expect(source).toContain('{#key teamId}');
	});
});
