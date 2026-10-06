// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { slideAfterKey } from './slides';

const press = (key: string, extra: Record<string, unknown> = {}) => ({
	key,
	ctrlKey: false,
	metaKey: false,
	altKey: false,
	shiftKey: false,
	target: document.body,
	...extra
});

describe('slideAfterKey', () => {
	it('moves one slide with the right and left arrows', () => {
		expect(slideAfterKey(press('ArrowRight'), 1, 5)).toBe(2);
		expect(slideAfterKey(press('ArrowLeft'), 1, 5)).toBe(0);
	});

	it('stops at the first and last slide instead of wrapping around', () => {
		expect(slideAfterKey(press('ArrowLeft'), 0, 5)).toBeNull();
		expect(slideAfterKey(press('ArrowRight'), 4, 5)).toBeNull();
	});

	it('ignores other keys, key combinations and a deck with a single slide', () => {
		expect(slideAfterKey(press('ArrowUp'), 1, 5)).toBeNull();
		expect(slideAfterKey(press('a'), 1, 5)).toBeNull();
		expect(slideAfterKey(press('ArrowRight', { metaKey: true }), 1, 5)).toBeNull();
		expect(slideAfterKey(press('ArrowRight', { shiftKey: true }), 1, 5)).toBeNull();
		expect(slideAfterKey(press('ArrowRight'), 0, 1)).toBeNull();
	});

	it('leaves the arrows to a text field that has text in it, but not to an empty one', () => {
		const textarea = document.createElement('textarea');
		expect(slideAfterKey(press('ArrowRight', { target: textarea }), 1, 5)).toBe(2);

		textarea.value = 'hello';
		expect(slideAfterKey(press('ArrowRight', { target: textarea }), 1, 5)).toBeNull();
	});

	it('does the same for an editable chat input', () => {
		const editable = document.createElement('div');
		Object.defineProperty(editable, 'isContentEditable', { value: true });
		expect(slideAfterKey(press('ArrowLeft', { target: editable }), 2, 5)).toBe(1);

		editable.textContent = 'hello';
		expect(slideAfterKey(press('ArrowLeft', { target: editable }), 2, 5)).toBeNull();
	});
});
