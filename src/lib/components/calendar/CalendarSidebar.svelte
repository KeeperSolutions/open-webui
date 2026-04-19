<script lang="ts">
	import { getContext } from 'svelte';
	import type { CalendarModel } from '$lib/apis/calendar';

	const i18n = getContext('i18n');

	export let calendars: CalendarModel[] = [];
	export let visibleCalendarIds: Set<string> = new Set();
	export let currentDate: Date = new Date();
	export let onToggle: (id: string) => void = () => {};
	export let onCreateCalendar: () => void = () => {};
	export let onDateSelect: (date: Date) => void = () => {};

	// Mini calendar state
	$: miniMonth = currentDate.getMonth();
	$: miniYear = currentDate.getFullYear();

	$: miniMonthStart = new Date(miniYear, miniMonth, 1);
	$: miniCalStart = (() => {
		const d = new Date(miniMonthStart);
		d.setDate(d.getDate() - d.getDay());
		return d;
	})();

	$: miniDays = (() => {
		const days: Date[] = [];
		const d = new Date(miniCalStart);
		for (let i = 0; i < 42; i++) {
			days.push(new Date(d));
			d.setDate(d.getDate() + 1);
		}
		return days;
	})();

	$: miniMonthNames = [
		'January',
		'February',
		'March',
		'April',
		'May',
		'June',
		'July',
		'August',
		'September',
		'October',
		'November',
		'December'
	];

	function isToday(d: Date): boolean {
		return d.toDateString() === new Date().toDateString();
	}

	function isSelected(d: Date): boolean {
		return d.toDateString() === currentDate.toDateString();
	}

	function navigateMini(delta: number) {
		if (miniMonth + delta > 11) {
			miniMonth = 0;
			miniYear++;
		} else if (miniMonth + delta < 0) {
			miniMonth = 11;
			miniYear--;
		} else {
			miniMonth += delta;
		}
	}
</script>

<div class="flex flex-col gap-4">
	<!-- Mini Month Calendar -->
	<div>
		<div class="flex items-center justify-between px-1 mb-1.5 mt-2">
			<div class="text-xs font-medium">{miniMonthNames[miniMonth]} {miniYear}</div>
			<div class="flex items-center gap-0.5">
				<button
					class="p-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 transition"
					on:click={() => navigateMini(-1)}
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="2"
						stroke="currentColor"
						class="size-3"
						><path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M15.75 19.5 8.25 12l7.5-7.5"
						/></svg
					>
				</button>
				<button
					class="p-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 transition"
					on:click={() => navigateMini(1)}
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="2"
						stroke="currentColor"
						class="size-3"
						><path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="m8.25 4.5 7.5 7.5-7.5 7.5"
						/></svg
					>
				</button>
			</div>
		</div>

		<div class="grid grid-cols-7 text-center text-[10px] text-gray-400 dark:text-gray-500 mb-0.5">
			{#each ['S', 'M', 'T', 'W', 'T', 'F', 'S'] as d}
				<div class="py-0.5">{d}</div>
			{/each}
		</div>

		<div class="grid grid-cols-7 text-center text-xs">
			{#each miniDays as day}
				<button
					class="w-7 h-7 flex items-center justify-center rounded-full transition
						{day.getMonth() !== miniMonth ? 'text-gray-300 dark:text-gray-600' : ''}
						{isToday(day) ? 'bg-blue-500 text-white' : ''}
						{day.toDateString() === currentDate.toDateString() && !isToday(day)
						? 'bg-gray-200 dark:bg-gray-700'
						: ''}
						{!isToday(day) && day.toDateString() !== currentDate.toDateString()
						? 'hover:bg-gray-100 dark:hover:bg-gray-800'
						: ''}"
					on:click={() => onDateSelect(day)}
				>
					{day.getDate()}
				</button>
			{/each}
		</div>
	</div>

	<!-- Calendar List -->
	<div>
		<div class="flex items-center justify-between mb-1 px-1">
			<div class="text-[11px] text-gray-400 dark:text-gray-500 uppercase tracking-wider">
				{$i18n.t('Calendars')}
			</div>
		</div>

		{#each calendars as cal (cal.id)}
			<button
				class="flex items-center gap-2 px-2 py-1 rounded-lg text-xs transition
					hover:bg-gray-50 dark:hover:bg-gray-800/50 w-full text-left"
				on:click={() => onToggle(cal.id)}
			>
				<span
					class="shrink-0 size-2.5 rounded-full transition-opacity"
					style="background-color: {cal.color || '#3b82f6'}; opacity: {visibleCalendarIds.has(
						cal.id
					)
						? '1'
						: '0.25'};"
				></span>
				<span
					class="truncate flex-1 {visibleCalendarIds.has(cal.id)
						? ''
						: 'text-gray-400 dark:text-gray-500'}"
				>
					{cal.name}
				</span>
			</button>
		{/each}
	</div>
</div>
