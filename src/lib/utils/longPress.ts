// Press-and-hold gesture for touch devices, which have no hover to reveal a row's actions.
// Attach to an ancestor of the row's own click target, so the release can be swallowed
// in the capture phase before it reaches it.

const DEFAULT_DURATION_MS = 500;
const MOVE_TOLERANCE_PX = 10;

export type LongPressParams = {
	// Only armed while true, so pointer devices keep their native context menu.
	enabled?: boolean;
	// Defaults to `enabled`. Set it separately for rows that have no menu to open but
	// must not fall back to the browser's own either.
	suppressNativeMenu?: boolean;
	duration?: number;
	onLongPress: () => void;
};

export function longPress(node: HTMLElement, params: LongPressParams) {
	let current = params;

	let timeout: ReturnType<typeof setTimeout> | null = null;
	let origin: { x: number; y: number } | null = null;

	// Set once the hold fired, so the click that follows touchend does not act on the row.
	let handled = false;

	const isEnabled = () => current.enabled !== false;
	const suppressesNativeMenu = () => current.suppressNativeMenu ?? isEnabled();

	const cancel = () => {
		if (timeout) {
			clearTimeout(timeout);
			timeout = null;
		}

		origin = null;
	};

	const onTouchStart = (event: TouchEvent) => {
		cancel();
		handled = false;

		if (!isEnabled() || event.touches.length !== 1) return;

		const touch = event.touches[0];
		origin = { x: touch.clientX, y: touch.clientY };

		timeout = setTimeout(() => {
			timeout = null;
			origin = null;
			handled = true;

			current.onLongPress();
			navigator.vibrate?.(10);
		}, current.duration ?? DEFAULT_DURATION_MS);
	};

	// A press that turns into a scroll is not a long press.
	const onTouchMove = (event: TouchEvent) => {
		if (!origin) return;

		const touch = event.touches[0];
		if (!touch) return;

		if (
			Math.abs(touch.clientX - origin.x) > MOVE_TOLERANCE_PX ||
			Math.abs(touch.clientY - origin.y) > MOVE_TOLERANCE_PX
		) {
			cancel();
		}
	};

	// The hold belongs to this gesture, not to the browser's own link menu.
	const onContextMenu = (event: Event) => {
		if (!suppressesNativeMenu()) return;

		event.preventDefault();
	};

	const onClickCapture = (event: MouseEvent) => {
		if (!handled) return;

		handled = false;

		event.preventDefault();
		event.stopPropagation();
	};

	// Suppresses the iOS callout and text selection; both properties inherit to the row.
	const applyTouchStyles = () => {
		if (suppressesNativeMenu()) {
			node.style.setProperty('-webkit-touch-callout', 'none');
			node.style.setProperty('user-select', 'none');
		} else {
			node.style.removeProperty('-webkit-touch-callout');
			node.style.removeProperty('user-select');
		}
	};

	applyTouchStyles();

	node.addEventListener('touchstart', onTouchStart, { passive: true });
	node.addEventListener('touchmove', onTouchMove, { passive: true });
	node.addEventListener('touchend', cancel);
	node.addEventListener('touchcancel', cancel);
	node.addEventListener('contextmenu', onContextMenu);
	node.addEventListener('click', onClickCapture, true);

	return {
		update(next: LongPressParams) {
			current = next;
			applyTouchStyles();

			if (!isEnabled()) {
				cancel();
				handled = false;
			}
		},
		destroy() {
			cancel();

			node.removeEventListener('touchstart', onTouchStart);
			node.removeEventListener('touchmove', onTouchMove);
			node.removeEventListener('touchend', cancel);
			node.removeEventListener('touchcancel', cancel);
			node.removeEventListener('contextmenu', onContextMenu);
			node.removeEventListener('click', onClickCapture, true);
		}
	};
}
