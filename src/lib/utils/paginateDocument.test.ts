// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { paginateDocument } from './paginateDocument';

// jsdom has no layout, so each block's height comes from its data-h and the probe's height is their sum
const makeProbe = () => {
	const probe = document.createElement('div');
	Object.defineProperty(probe, 'scrollHeight', {
		get: () =>
			Array.from(probe.querySelectorAll('[data-h]')).reduce(
				(sum, el) => sum + Number(el.getAttribute('data-h')),
				0
			)
	});
	return probe;
};

const pageText = (page: string) => {
	const el = document.createElement('div');
	el.innerHTML = page;
	return Array.from(el.querySelectorAll('[data-h]')).map((node) => node.textContent);
};

describe('paginateDocument', () => {
	let probe: HTMLElement;

	beforeEach(() => {
		probe = makeProbe();
	});

	it('keeps a short document on one page', () => {
		const pages = paginateDocument('<p data-h="10">a</p><p data-h="10">b</p>', probe, 100);
		expect(pages.map(pageText)).toEqual([['a', 'b']]);
	});

	it('starts a new page when the next block does not fit', () => {
		const pages = paginateDocument(
			'<p data-h="60">a</p><p data-h="60">b</p><p data-h="30">c</p>',
			probe,
			100
		);
		expect(pages.map(pageText)).toEqual([['a'], ['b', 'c']]);
	});

	it('breaks on a manual page break and drops the empty paragraphs around it', () => {
		const pages = paginateDocument(
			'<p data-h="10">a</p><p></p><hr class="page-break"><p></p><p data-h="10">b</p>',
			probe,
			100
		);
		expect(pages.map(pageText)).toEqual([['a'], ['b']]);
	});

	it('moves a heading down with the block that follows it', () => {
		const pages = paginateDocument(
			'<p data-h="70">a</p><h2 data-h="10">h</h2><p data-h="40">b</p>',
			probe,
			100
		);
		expect(pages.map(pageText)).toEqual([['a'], ['h', 'b']]);
	});

	it('splits a long table by rows and repeats its header', () => {
		const rows = [1, 2, 3, 4].map((n) => `<tr data-h="30"><td>r${n}</td></tr>`).join('');
		const pages = paginateDocument(
			`<table><thead><tr data-h="10"><th>head</th></tr></thead><tbody>${rows}</tbody></table>`,
			probe,
			100
		);
		expect(pages.map(pageText)).toEqual([
			['head', 'r1', 'r2', 'r3'],
			['head', 'r4']
		]);
	});

	it('splits a long numbered list and keeps counting on the next page', () => {
		const items = [1, 2, 3].map((n) => `<li data-h="40">i${n}</li>`).join('');
		const pages = paginateDocument(`<ol>${items}</ol>`, probe, 100);
		expect(pages.map(pageText)).toEqual([['i1', 'i2'], ['i3']]);
		expect(pages[1]).toContain('start="3"');
	});

	it('gives a block taller than a page a page of its own', () => {
		const pages = paginateDocument(
			'<p data-h="10">a</p><p data-h="150">big</p><p data-h="10">b</p>',
			probe,
			100
		);
		expect(pages.map(pageText)).toEqual([['a'], ['big'], ['b']]);
	});
});
