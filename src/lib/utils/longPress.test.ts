// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { longPress } from './longPress';

const DURATION_MS = 500;
const CLICK_WINDOW_MS = 700;

function touchEvent(type: string, x = 0, y = 0) {
	const touch = { clientX: x, clientY: y } as Touch;
	return new (class extends Event {
		touches = [touch];
	})(type, { bubbles: true, cancelable: true });
}

function click(target: EventTarget) {
	const event = new MouseEvent('click', { bubbles: true, cancelable: true });
	target.dispatchEvent(event);
	return event;
}

describe('longPress', () => {
	let node: HTMLElement;
	let onLongPress: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		vi.useFakeTimers();
		node = document.createElement('div');
		document.body.appendChild(node);
		onLongPress = vi.fn();
	});

	afterEach(() => {
		node.remove();
		vi.useRealTimers();
	});

	it('fires onLongPress after the threshold with no movement', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).toHaveBeenCalledTimes(1);
	});

	it('does not fire before the threshold elapses', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS - 1);

		expect(onLongPress).not.toHaveBeenCalled();
	});

	it('does not fire when disabled', () => {
		longPress(node, { enabled: false, onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).not.toHaveBeenCalled();
	});

	it('does not fire when more than one touch is active', () => {
		longPress(node, { onLongPress });

		const event = new (class extends Event {
			touches = [
				{ clientX: 0, clientY: 0 } as Touch,
				{ clientX: 10, clientY: 10 } as Touch
			];
		})('touchstart', { bubbles: true });
		node.dispatchEvent(event);
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).not.toHaveBeenCalled();
	});

	it('cancels the pending long press once the touch moves past the tolerance', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart', 0, 0));
		node.dispatchEvent(touchEvent('touchmove', 20, 0));
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).not.toHaveBeenCalled();
	});

	it('keeps the long press armed for movement within the tolerance', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart', 0, 0));
		node.dispatchEvent(touchEvent('touchmove', 5, 5));
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).toHaveBeenCalledTimes(1);
	});

	it('cancels the pending long press on touchcancel', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		node.dispatchEvent(new Event('touchcancel', { bubbles: true }));
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).not.toHaveBeenCalled();
	});

	it('cancels the pending long press on touchend before the threshold', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		node.dispatchEvent(new Event('touchend', { bubbles: true }));
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).not.toHaveBeenCalled();
	});

	it('swallows the synthetic click that follows touchend after a long press', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);
		node.dispatchEvent(new Event('touchend', { bubbles: true }));

		const event = click(node);

		expect(event.defaultPrevented).toBe(true);
	});

	it('stops swallowing clicks once the click window elapses', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);
		node.dispatchEvent(new Event('touchend', { bubbles: true }));

		vi.advanceTimersByTime(CLICK_WINDOW_MS);
		const event = click(node);

		expect(event.defaultPrevented).toBe(false);
	});

	it('does not swallow a click when the hold never turned into a long press', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		node.dispatchEvent(new Event('touchend', { bubbles: true }));

		const event = click(node);

		expect(event.defaultPrevented).toBe(false);
	});

	it('suppresses the native context menu while enabled', () => {
		longPress(node, { onLongPress });

		const event = new Event('contextmenu', { bubbles: true, cancelable: true });
		node.dispatchEvent(event);

		expect(event.defaultPrevented).toBe(true);
	});

	it('leaves the native context menu alone when suppressNativeMenu is false', () => {
		longPress(node, { onLongPress, suppressNativeMenu: false });

		const event = new Event('contextmenu', { bubbles: true, cancelable: true });
		node.dispatchEvent(event);

		expect(event.defaultPrevented).toBe(false);
	});

	it('update() applies a new enabled value to future gestures', () => {
		const action = longPress(node, { onLongPress, enabled: false });

		action.update({ onLongPress, enabled: true });
		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).toHaveBeenCalledTimes(1);
	});

	it('update() to disabled cancels a pending long press', () => {
		const action = longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		action.update({ onLongPress, enabled: false });
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).not.toHaveBeenCalled();
	});

	it('destroy() cancels a pending long press and removes listeners', () => {
		const action = longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		action.destroy();
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).not.toHaveBeenCalled();

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).not.toHaveBeenCalled();
	});

	it('destroy() stops swallowing an in-flight click', () => {
		const action = longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);
		node.dispatchEvent(new Event('touchend', { bubbles: true }));

		action.destroy();
		const event = click(node);

		expect(event.defaultPrevented).toBe(false);
	});

	// jsdom's CSS engine does not recognize `-webkit-touch-callout`, so only user-select is
	// observable here; touch-callout suppression itself is exercised via manual phone testing.
	it('applies user-select suppression while enabled', () => {
		longPress(node, { onLongPress });

		expect(node.style.getPropertyValue('user-select')).toBe('none');
	});

	it('removes user-select suppression when suppressNativeMenu is false', () => {
		longPress(node, { onLongPress, suppressNativeMenu: false });

		expect(node.style.getPropertyValue('user-select')).toBe('');
	});

	it('update() re-evaluates user-select suppression', () => {
		const action = longPress(node, { onLongPress, suppressNativeMenu: false });

		action.update({ onLongPress, suppressNativeMenu: true });

		expect(node.style.getPropertyValue('user-select')).toBe('none');
	});

	it('does not swallow a click on an unrelated element after a long press', () => {
		const other = document.createElement('div');
		document.body.appendChild(other);

		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);
		node.dispatchEvent(new Event('touchend', { bubbles: true }));

		const event = click(other);

		expect(event.defaultPrevented).toBe(false);

		other.remove();
	});

	it('stops propagation of the swallowed synthetic click', () => {
		const outerHandler = vi.fn();
		document.body.addEventListener('click', outerHandler, true);

		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);
		node.dispatchEvent(new Event('touchend', { bubbles: true }));

		click(node);

		expect(outerHandler).not.toHaveBeenCalled();

		document.body.removeEventListener('click', outerHandler, true);
	});

	it('does not leak a pending swallow timeout across a second touchstart', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);
		node.dispatchEvent(new Event('touchend', { bubbles: true }));

		node.dispatchEvent(touchEvent('touchstart'));
		node.dispatchEvent(new Event('touchend', { bubbles: true }));

		const event = click(node);

		expect(event.defaultPrevented).toBe(false);
	});

	it('honors a custom duration', () => {
		longPress(node, { onLongPress, duration: 1000 });

		node.dispatchEvent(touchEvent('touchstart'));
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).not.toHaveBeenCalled();

		vi.advanceTimersByTime(500);

		expect(onLongPress).toHaveBeenCalledTimes(1);
	});

	it('does not suppress the native context menu when disabled without an explicit suppressNativeMenu', () => {
		longPress(node, { onLongPress, enabled: false });

		const event = new Event('contextmenu', { bubbles: true, cancelable: true });
		node.dispatchEvent(event);

		expect(event.defaultPrevented).toBe(false);
	});

	it('does not cancel on movement exactly at the tolerance boundary', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart', 0, 0));
		node.dispatchEvent(touchEvent('touchmove', 10, 0));
		vi.advanceTimersByTime(DURATION_MS);

		expect(onLongPress).toHaveBeenCalledTimes(1);
	});

	it('uses the callback from the latest update() if it changes mid-press', () => {
		const staleOnLongPress = vi.fn();
		const freshOnLongPress = vi.fn();
		const action = longPress(node, { onLongPress: staleOnLongPress });

		node.dispatchEvent(touchEvent('touchstart'));
		action.update({ onLongPress: freshOnLongPress });
		vi.advanceTimersByTime(DURATION_MS);

		expect(staleOnLongPress).not.toHaveBeenCalled();
		expect(freshOnLongPress).toHaveBeenCalledTimes(1);
	});

	it('does not throw on touchmove with no active touch', () => {
		longPress(node, { onLongPress });

		node.dispatchEvent(touchEvent('touchstart'));

		const event = new (class extends Event {
			touches: Touch[] = [];
		})('touchmove', { bubbles: true });

		expect(() => node.dispatchEvent(event)).not.toThrow();

		vi.advanceTimersByTime(DURATION_MS);
		expect(onLongPress).toHaveBeenCalledTimes(1);
	});
});
