// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';

import HgInput from './HgInput.svelte';

beforeEach(() => vi.clearAllMocks());

const renderInput = (props = {}) => render(HgInput, { props });

// ─── label / id wiring ────────────────────────────────────────────────────────

describe('label and id', () => {
	it('associates label with input via name when id is omitted', () => {
		renderInput({ label: 'Email', name: 'email', type: 'email' });
		expect(screen.getByLabelText('Email')).toBeInTheDocument();
	});

	it('prefers explicit id over name for label association', () => {
		renderInput({ label: 'Email', name: 'email-name', id: 'email-id', type: 'email' });
		expect(screen.getByLabelText('Email').id).toBe('email-id');
	});

	it('renders no label element when label prop is empty', () => {
		const { container } = renderInput({ name: 'email', type: 'email' });
		expect(container.querySelector('label')).not.toBeInTheDocument();
	});

	it('renders caption text when provided', () => {
		renderInput({ name: 'email', caption: 'We will never share your email.' });
		expect(screen.getByText('We will never share your email.')).toBeInTheDocument();
	});

	it('renders no caption when prop is omitted', () => {
		renderInput({ name: 'email' });
		// just verify no unexpected paragraph appears
		expect(screen.queryByText(/never share/)).not.toBeInTheDocument();
	});
});

// ─── input type and attributes ────────────────────────────────────────────────

describe('input attributes', () => {
	it('sets type=email on the input element', () => {
		renderInput({ name: 'email', type: 'email' });
		expect(screen.getByRole('textbox')).toHaveAttribute('type', 'email');
	});

	it('sets type=password on the input element', () => {
		renderInput({ name: 'password', label: 'Password', type: 'password' });
		// password inputs have no implicit ARIA role — query by label
		expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
	});

	it('forwards the placeholder prop', () => {
		renderInput({ name: 'email', type: 'email', placeholder: 'jane@company.com' });
		expect(screen.getByRole('textbox')).toHaveAttribute('placeholder', 'jane@company.com');
	});

	it('sets required attribute when prop is true', () => {
		renderInput({ name: 'email', type: 'email', required: true });
		expect(screen.getByRole('textbox')).toBeRequired();
	});

	it('is not required by default', () => {
		renderInput({ name: 'email', type: 'email' });
		expect(screen.getByRole('textbox')).not.toBeRequired();
	});

	it('disables the input when disabled prop is true', () => {
		renderInput({ name: 'email', type: 'email', disabled: true });
		expect(screen.getByRole('textbox')).toBeDisabled();
	});
});

// ─── error state ──────────────────────────────────────────────────────────────

describe('error state', () => {
	it('applies error border class when error=true', () => {
		renderInput({ name: 'email', type: 'email', error: true });
		const wrapper = screen.getByRole('textbox').closest('div')!;
		expect(wrapper.className).toMatch(/border-hg-error-text/);
	});

	it('does not apply error border class when error=false', () => {
		renderInput({ name: 'email', type: 'email', error: false });
		const wrapper = screen.getByRole('textbox').closest('div')!;
		expect(wrapper.className).not.toMatch(/border-hg-error-text/);
	});

	it('applies error color to caption when error=true', () => {
		renderInput({ name: 'email', type: 'email', error: true, caption: 'Invalid email' });
		const caption = screen.getByText('Invalid email');
		expect(caption.className).toMatch(/text-hg-error-text/);
	});

	it('applies muted border class when disabled', () => {
		renderInput({ name: 'email', type: 'email', disabled: true });
		const wrapper = screen.getByRole('textbox').closest('div')!;
		expect(wrapper.className).toMatch(/border-hg-border-subtle/);
	});
});

// ─── value binding and events ─────────────────────────────────────────────────

describe('value and events', () => {
	it('reflects an initial value', () => {
		renderInput({ name: 'email', type: 'email', value: 'preset@example.com' });
		expect(screen.getByRole('textbox')).toHaveValue('preset@example.com');
	});

	it('fires the input event when the user types', async () => {
		const onInput = vi.fn();
		const { container } = renderInput({ name: 'email', type: 'email' });
		container.querySelector('input')!.addEventListener('input', onInput);

		await fireEvent.input(screen.getByRole('textbox'), { target: { value: 'test@example.com' } });

		expect(onInput).toHaveBeenCalled();
	});
});

// ─── password reveal toggle ───────────────────────────────────────────────────

describe('password reveal toggle', () => {
	const getInput = (container: HTMLElement) => container.querySelector('input')!;

	it('renders a reveal toggle for password fields, hidden by default', () => {
		renderInput({ name: 'password', type: 'password' });
		// Default state: password hidden → "Show password" affordance
		expect(screen.getByRole('button', { name: 'Show password' })).toBeInTheDocument();
	});

	it('does not render a reveal toggle for non-password fields', () => {
		renderInput({ name: 'email', type: 'email' });
		expect(screen.queryByRole('button', { name: /password/i })).toBeNull();
	});

	it('starts with the input type as password (masked)', () => {
		const { container } = renderInput({ name: 'password', type: 'password' });
		expect(getInput(container).type).toBe('password');
	});

	it('reveals the password (type=text) when the toggle is clicked', async () => {
		const { container } = renderInput({ name: 'password', type: 'password' });
		await fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
		expect(getInput(container).type).toBe('text');
	});

	it('flips the toggle label and aria-pressed when revealed', async () => {
		renderInput({ name: 'password', type: 'password' });
		const toggle = screen.getByRole('button', { name: 'Show password' });
		expect(toggle).toHaveAttribute('aria-pressed', 'false');

		await fireEvent.click(toggle);

		const hideToggle = screen.getByRole('button', { name: 'Hide password' });
		expect(hideToggle).toHaveAttribute('aria-pressed', 'true');
	});

	it('hides the password again on a second click', async () => {
		const { container } = renderInput({ name: 'password', type: 'password' });
		await fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Hide password' }));
		expect(getInput(container).type).toBe('password');
	});

	it('preserves the entered value across reveal toggling', async () => {
		const { container } = renderInput({ name: 'password', type: 'password', value: 's3cret' });
		await fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
		expect(getInput(container)).toHaveValue('s3cret');
	});

	it('omits the toggle when showRevealToggle=false', () => {
		renderInput({ name: 'password', type: 'password', showRevealToggle: false });
		expect(screen.queryByRole('button', { name: /password/i })).toBeNull();
	});
});
