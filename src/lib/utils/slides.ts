type SlideKeyEvent = Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'> & {
	target: EventTarget | null;
};

// A text field that has text in it needs the arrow keys to move its cursor, an empty one doesn't
const isTypingIn = (target: EventTarget | null) => {
	const element = target as HTMLElement | null;
	if (!element?.tagName) return false;
	if (element.isContentEditable) return (element.textContent ?? '').trim() !== '';
	if (['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName)) {
		return ((element as HTMLInputElement).value ?? '') !== '';
	}
	return false;
};

// The slide to show after this key press, or null when the key should leave the slide as it is
export const slideAfterKey = (event: SlideKeyEvent, current: number, total: number): number | null => {
	if (total < 2 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return null;
	if (isTypingIn(event.target)) return null;

	const next = event.key === 'ArrowRight' ? current + 1 : event.key === 'ArrowLeft' ? current - 1 : current;
	return next === current || next < 0 || next >= total ? null : next;
};
