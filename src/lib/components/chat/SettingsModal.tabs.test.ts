// @vitest-environment node
/**
 * Every personal settings tab declared in `allSettings` must have a sidebar
 * button, a content panel and a sidebar group. A tab missing the button or the
 * panel is listed and searchable but never shown, so its settings become
 * unreachable without any error. A tab missing a group lands under a stray
 * "General" heading.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const source = readFileSync(
	resolve(process.cwd(), 'src/lib/components/chat/SettingsModal.svelte'),
	'utf-8'
);

const allSettingsBlock = source.slice(
	source.indexOf('const allSettings'),
	source.indexOf('const adminSettings')
);
const tabIds = [...allSettingsBlock.matchAll(/^\t\t\tid: '([^']+)'/gm)].map((m) => m[1]);

const groupsBlock = source.slice(
	source.indexOf('const personalSettingGroups'),
	source.indexOf('const adminSettingGroups')
);

describe('SettingsModal personal tabs', () => {
	it('declares the tabs it is checked against', () => {
		expect(tabIds).toContain('general');
		expect(tabIds).toContain('privacy');
	});

	it.each(tabIds)('renders a button and a panel for "%s"', (id) => {
		expect(source).toContain(`tabId === '${id}'`);
		expect(source).toContain(`selectedTab === '${id}'`);
	});

	it.each(tabIds)('assigns "%s" to a sidebar group', (id) => {
		expect(groupsBlock).toMatch(new RegExp(`^\\t\\t${id}: '`, 'm'));
	});
});
