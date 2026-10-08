<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import HgIconShield from '$lib/components/icons/HgIconShield.svelte';
	import { setPiiMaskingDefault } from '$lib/apis/users';
	import { config } from '$lib/stores';
	import { getStoredPiiMasking } from '$lib/utils/pii';
	import Toggle from './parts/Toggle.svelte';
	import {
		defaultOffCountKey,
		maskingStateOf,
		RELOAD_NOTE,
		type AccessUser
	} from './sections/usersAccess';

	const i18n: Writable<i18nType> = getContext('i18n');

	/** The directory the confirmation counts. */
	export let users: AccessUser[] = [];
	/** The directory is cut off, so the count is a lower bound. */
	export let truncated = false;
	/** The directory is not loaded, so the confirmation would count no users. */
	export let disabled = false;
	/** Called after the default was saved, so the sections can reload. */
	export let onChanged: () => void = () => {};

	// Anything but an explicit `false` reads as ON.
	$: enabled = $config?.features?.pii_masking_default !== false;

	let saving = false;
	let confirmOff = false;

	$: followingDefault = users.filter(
		(u) =>
			maskingStateOf(
				u.pii_masking_enforced === true,
				getStoredPiiMasking(u.settings?.ui ?? {}),
				true
			) === 'default-on'
	).length;

	const apply = async (next: boolean) => {
		saving = true;
		try {
			await setPiiMaskingDefault(localStorage.token, next);
			config.update((c) =>
				c ? { ...c, features: { ...c.features, pii_masking_default: next } } : c
			);
			toast.success(`${$i18n.t('Default PII masking updated.')} ${$i18n.t(RELOAD_NOTE)}`);
			onChanged();
		} catch (e) {
			toast.error(`${e}`);
		} finally {
			saving = false;
		}
	};

	// Turning the default off unmasks every user who has not chosen, so it asks first.
	const toggle = () => {
		if (disabled) return;
		if (enabled) confirmOff = true;
		else apply(true);
	};
</script>

<div
	data-testid="pii-default-control"
	title={$i18n.t('PII masking for users who have not chosen a setting')}
	class="flex items-center gap-2 rounded-full border border-pii-line bg-pii-white py-[7px] pr-2 pl-3 text-[13px] font-medium leading-[1.55] whitespace-nowrap text-pii-ink"
>
	<!-- Same padding, border and line height as PeriodPill, so both are the same
	     height. The toggle is 21px, taller than the 20px text line, so its wrapper
	     takes 1px off each side to keep it from growing the pill. -->
	<HgIconShield class="size-4 text-pii-muted" />
	<span>{$i18n.t('PII Masking')}</span>
	<span class="-my-px flex">
		<Toggle
			on={enabled}
			disabled={saving || disabled}
			ariaLabel={$i18n.t('PII masking for users who have not chosen a setting')}
			on:click={toggle}
		/>
	</span>
</div>

<ConfirmDialog
	bind:show={confirmOff}
	title={$i18n.t('Turn off PII masking by default?')}
	message={`${$i18n.t(defaultOffCountKey(followingDefault, truncated), {
		count: followingDefault
	})} ${$i18n.t('Users can still turn masking on themselves, and enforced users stay masked.')} ${$i18n.t(RELOAD_NOTE)}`}
	on:confirm={() => apply(false)}
/>
